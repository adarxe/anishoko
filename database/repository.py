import json
import logging
from database.connection import get_connection

logger = logging.getLogger("ShokoAniSync")

# ==========================================
# CACHÉ DE RELACIONES (Árboles de Franquicia)
# ==========================================

def find_anilist_id_in_mirror_by_title(clean_title):
    """Busca un anilist_id en la tabla espejo local comparando el titulo limpio."""
    with get_connection() as conn:
        # Busca coincidencia exacta o parecida en titulo romaji u ingles
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
    """Obtiene el arbol relacional solo si la cache tiene menos de 7 dias (168h)."""
    with get_connection() as conn:
        row = conn.execute('''
            SELECT related_ids_json 
            FROM relations_cache 
            WHERE base_anilist_id = ? 
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
            INSERT INTO relations_cache (base_anilist_id, related_ids_json, cached_at)
            VALUES (?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT (base_anilist_id) DO UPDATE SET
                related_ids_json = EXCLUDED.related_ids_json,
                cached_at = CURRENT_TIMESTAMP
        ''', (base_anilist_id, json.dumps(related_ids)))

# ==========================================
# MAPEO DIRECTO (Caché L1)
# ==========================================
def get_mapping(shoko_series_id, episode):
    with get_connection() as conn:
        row = conn.execute("SELECT anilist_id FROM series_mapping WHERE shoko_series_id = ? AND episode = ?", (shoko_series_id, episode)).fetchone()
        if row:
            return row[0]
        return None

def update_mirror_local_watch(anilist_id, episode_watched, user_status="CURRENT", repeat_count=0):
    """Actualiza el progreso, estado y contador de rewatch en el espejo local."""
    with get_connection() as conn:
        conn.execute('''
            UPDATE anilist_mirror 
            SET episodes_watched = ?, 
                user_status = ?, 
                repeat_count = ?,
                last_watched_at = CURRENT_TIMESTAMP
            WHERE anilist_id = ?
        ''', (episode_watched, user_status, repeat_count, anilist_id))

def save_mapping(shoko_series_id, episode, anilist_id, search_query, romaji_name):
    with get_connection() as conn:
        conn.execute('''
            INSERT INTO series_mapping (shoko_series_id, episode, anilist_id, search_query, romaji_name, last_updated)
            VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT (shoko_series_id, episode) DO UPDATE SET
                anilist_id = EXCLUDED.anilist_id,
                search_query = EXCLUDED.search_query,
                romaji_name = EXCLUDED.romaji_name,
                last_updated = CURRENT_TIMESTAMP
        ''', (shoko_series_id, episode, anilist_id, search_query, romaji_name))

def update_mirror_local_watch(anilist_id, episode_watched, user_status="CURRENT"):
    """Actualiza la obra en el espejo local registrando la fecha de reproduccion local exacta."""
    with get_connection() as conn:
        conn.execute('''
            UPDATE anilist_mirror 
            SET episodes_watched = ?, 
                user_status = ?, 
                last_watched_at = CURRENT_TIMESTAMP
            WHERE anilist_id = ?
        ''', (episode_watched, user_status, anilist_id))


def add_to_watch_history(anilist_id, episode, shoko_series_id=""):
    """Registra una entrada inmutable en el historial cronológico de reproducción (Event Sourcing)."""
    with get_connection() as conn:
        conn.execute('''
            INSERT INTO watch_history (anilist_id, episode, shoko_series_id)
            VALUES (?, ?, ?)
        ''', (anilist_id, episode, shoko_series_id))

# ==========================================
# GESTIÓN DE COLA (Workers)
# ==========================================
def add_to_queue(shoko_series_id, anilist_id, episode, search_query, series_name):
    with get_connection() as conn:
        conn.execute('''
            INSERT INTO queue (shoko_series_id, anilist_id, episode, search_query, series_name)
            VALUES (?, ?, ?, ?, ?)
        ''', (shoko_series_id, anilist_id, episode, search_query, series_name))

def get_queue():
    with get_connection() as conn:
        cursor = conn.execute("SELECT id, shoko_series_id, anilist_id, episode, search_query, series_name FROM queue ORDER BY created_at ASC")
        cols = [desc[0] for desc in cursor.description]
        rows = cursor.fetchall()
        return [dict(zip(cols, row)) for row in rows]

def remove_from_queue(queue_id):
    with get_connection() as conn:
        conn.execute("DELETE FROM queue WHERE id = ?", (queue_id,))

