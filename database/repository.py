import json
import logging
from database.connection import get_connection

logger = logging.getLogger("ShokoAniSync")

# ==========================================
# CACHÉ DE RELACIONES (Árboles de Franquicia)
# ==========================================

def find_anilist_id_in_mirror_by_title(clean_title):
    with get_connection() as conn:
        row = conn.execute('''
            SELECT anilist_id FROM anilist_mirror 
            WHERE LOWER(title_romaji) = LOWER(?) 
               OR LOWER(title_english) = LOWER(?)
            LIMIT 1
        ''', (clean_title, clean_title)).fetchone()
        if row:
            return row[0]
    return None

def get_cached_relations(base_anilist_id):
    with get_connection() as conn:
        row = conn.execute('''
            SELECT relations_json 
            FROM relations_cache 
            WHERE seed_id = ? 
              AND cached_at >= datetime('now', '-7 days')
        ''', (base_anilist_id,)).fetchone()
        if row:
            try:
                return json.loads(row[0])
            except json.JSONDecodeError:
                return []
    return None

def save_cached_relations(base_anilist_id, related_ids):
    with get_connection() as conn:
        conn.execute('''
            INSERT INTO relations_cache (seed_id, relations_json, cached_at)
            VALUES (?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT (seed_id) DO UPDATE SET
                relations_json = EXCLUDED.relations_json,
                cached_at = CURRENT_TIMESTAMP
        ''', (base_anilist_id, json.dumps(related_ids)))

def get_all_mirror_entries():
    with get_connection() as conn:
        return conn.execute("SELECT anilist_id, title_romaji, title_english FROM anilist_mirror").fetchall()

def get_mirror_entry_by_id(anilist_id):
    """Obtiene metadatos y progreso actual de una obra en el espejo local a 0 ms."""
    with get_connection() as conn:
        row = conn.execute('''
            SELECT anilist_id as id, title_romaji, title_english, total_episodes, episodes_watched, user_status, format
            FROM anilist_mirror WHERE anilist_id = ?
        ''', (anilist_id,)).fetchone()
        
        if row:
            return {
                "id": row[0],
                "title_romaji": row[1],
                "title_english": row[2],
                "total_episodes": row[3] or 0,
                "user_progress": row[4] or 0,
                "user_status": row[5] or "PLANNING",
                "format": row[6] or "TV"
            }
        return None

# ==========================================
# MAPEO DIRECTO (Caché L1)
# ==========================================

def get_mapping(anidb_id, episode):
    with get_connection() as conn:
        row = conn.execute('''
            SELECT anilist_id 
            FROM series_mapping 
            WHERE anidb_id = ? AND episode = ?
              AND last_updated >= datetime('now', '-30 days')
            LIMIT 1
        ''', (anidb_id, episode)).fetchone()
        if row:
            return row[0]
    return None

def update_mirror_local_watch(anilist_id, episode_watched, user_status="CURRENT", repeat_count=0):
    with get_connection() as conn:
        conn.execute('''
            UPDATE anilist_mirror 
            SET episodes_watched = ?, 
                user_status = ?, 
                repeat_count = ?,
                last_synced = CURRENT_TIMESTAMP
            WHERE anilist_id = ?
        ''', (episode_watched, user_status, repeat_count, anilist_id))

def save_mapping(anidb_id, episode, anilist_id, search_query, romaji_name):
    with get_connection() as conn:
        conn.execute('''
            INSERT INTO series_mapping (anidb_id, episode, anilist_id, search_query, romaji_name, last_updated)
            VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT (anidb_id, episode) DO UPDATE SET
                anilist_id = EXCLUDED.anilist_id,
                search_query = EXCLUDED.search_query,
                romaji_name = EXCLUDED.romaji_name,
                last_updated = CURRENT_TIMESTAMP
        ''', (anidb_id, episode, anilist_id, search_query, romaji_name))

def add_to_watch_history(anidb_id, episode, anilist_id, series_name=""):
    with get_connection() as conn:
        conn.execute('''
            INSERT INTO watch_history (anidb_id, episode, anilist_id, series_name)
            VALUES (?, ?, ?, ?)
        ''', (anidb_id, episode, anilist_id, series_name))

# ==========================================
# GESTIÓN DE COLA (Workers)
# ==========================================

def get_queue():
    with get_connection() as conn:
        cursor = conn.execute("SELECT id, anidb_id, anilist_id, episode, search_query, series_name, status, attempts, created_at FROM queue")
        cols = [desc[0] for desc in cursor.description]
        return [dict(zip(cols, row)) for row in cursor.fetchall()]

def remove_from_queue(queue_id):
    with get_connection() as conn:
        conn.execute("DELETE FROM queue WHERE id = ?", (queue_id,))

def add_to_queue(anidb_id, anilist_id, episode, search_query="", series_name="", shoko_id=""):
    with get_connection() as conn:
        conn.execute('''
            INSERT INTO queue (anidb_id, shoko_id, anilist_id, episode, search_query, series_name)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (anidb_id, str(shoko_id or ""), anilist_id, episode, search_query, series_name))

def update_queue_status(queue_id, status):
    with get_connection() as conn:
        conn.execute("UPDATE queue SET status = ? WHERE id = ?", (status, queue_id))

