import json
import logging
from database.connection import get_connection

logger = logging.getLogger("ShokoAniSync")

# ==========================================
# CACHÉ DE RELACIONES (Árboles de Franquicia)
# ==========================================
def get_cached_relations(base_anilist_id):
    with get_connection() as conn:
        row = conn.execute("SELECT related_ids_json FROM relations_cache WHERE base_anilist_id = ?", (base_anilist_id,)).fetchone()
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

