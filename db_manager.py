import sqlite3
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "shoko_sync.db"

def get_connection():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.execute('pragma journal_mode=wal')
    return conn

def init_db():
    conn = get_connection()
    cursor = conn.cursor()

    # Tabla con soporte para ambos IDs y el AniList definitivo
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS series_mapping (
            shoko_series_id INTEGER PRIMARY KEY,
            anidb_id INTEGER,
            mal_id INTEGER,
            anilist_id INTEGER,
            romaji_name TEXT,
            current_episode INTEGER,
            last_watched_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # Living Offline adaptado para usar anilist_id
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS retry_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            shoko_series_id INTEGER,
            anilist_id INTEGER,
            episode INTEGER,
            added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    conn.commit()
    conn.close()
    print("🗄️ Base de datos inicializada.")

def get_series_data(shoko_series_id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT anilist_id, current_episode FROM series_mapping WHERE shoko_series_id = ?', (shoko_series_id,))
    row = cursor.fetchone()
    conn.close()
    if row:
        return {"anilist_id": row[0], "current_episode": row[1]}
    return None

def save_new_series(shoko_series_id, anidb_id, mal_id, anilist_id, romaji_name, current_episode):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('''
        INSERT OR IGNORE INTO series_mapping (shoko_series_id, anidb_id, mal_id, anilist_id, romaji_name, current_episode)
        VALUES (?, ?, ?, ?, ?, ?)
    ''', (shoko_series_id, anidb_id, mal_id, anilist_id, romaji_name, current_episode))
    conn.commit()
    conn.close()

def update_episode_progress(shoko_series_id, new_episode):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('''
        UPDATE series_mapping 
        SET current_episode = ?, last_watched_date = CURRENT_TIMESTAMP, last_updated = CURRENT_TIMESTAMP
        WHERE shoko_series_id = ?
    ''', (new_episode, shoko_series_id))
    conn.commit()
    conn.close()

def add_to_queue(shoko_series_id, anilist_id, episode):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT id FROM retry_queue WHERE shoko_series_id = ? AND episode = ?
    ''', (shoko_series_id, episode))
    if not cursor.fetchone():
        cursor.execute('''
            INSERT INTO retry_queue (shoko_series_id, anilist_id, episode)
            VALUES (?, ?, ?)
        ''', (shoko_series_id, anilist_id, episode))
        conn.commit()
    conn.close()

def get_pending_retries():
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT id, shoko_series_id, anilist_id, episode FROM retry_queue ORDER BY added_at ASC')
    rows = cursor.fetchall()
    conn.close()
    return [{"id": r[0], "shoko_series_id": r[1], "anilist_id": r[2], "episode": r[3]} for r in rows]

def remove_from_queue(queue_id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('DELETE FROM retry_queue WHERE id = ?', (queue_id,))
    conn.commit()
    conn.close()

if __name__ == "__main__":
    init_db()
