import sqlite3
import logging
from config import DB_FILE

logger = logging.getLogger("ShokoAniSync")

def get_connection():
    # timeout=20.0 evita bloqueos cuando el CronSync y el Webhook escriben simultáneamente
    conn = sqlite3.connect(str(DB_FILE), timeout=20.0, check_same_thread=False)
    conn.execute('PRAGMA journal_mode=WAL;') 
    return conn

def init_db():
    """Inicializa tablas y asegura tipos de datos correctos."""
    with get_connection() as conn:
        conn.execute('''
            CREATE TABLE IF NOT EXISTS anilist_mirror (
                anilist_id INTEGER PRIMARY KEY,
                mal_id INTEGER,
                title_romaji VARCHAR,
                title_english VARCHAR,
                format VARCHAR,
                status VARCHAR,
                user_status VARCHAR,
                episodes_watched INTEGER,
                total_episodes INTEGER,
                repeat_count INTEGER DEFAULT 0,
                last_synced TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_watched_at TIMESTAMP
            )
        ''')

        conn.execute('''
            CREATE TABLE IF NOT EXISTS watch_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                anilist_id INTEGER NOT NULL,
                shoko_series_id TEXT,
                episode INTEGER NOT NULL,
                watched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # CAMBIO: shoko_series_id pasa de INTEGER a TEXT
        conn.execute('''
            CREATE TABLE IF NOT EXISTS series_mapping (
                shoko_series_id TEXT,
                episode INTEGER,
                anilist_id INTEGER,
                search_query VARCHAR,
                romaji_name VARCHAR,
                last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_watched_at TIMESTAMP,
                PRIMARY KEY (shoko_series_id, episode)
            )
        ''')

        conn.execute('''
            CREATE TABLE IF NOT EXISTS relations_cache (
                base_anilist_id INTEGER PRIMARY KEY,
                related_ids_json VARCHAR,
                cached_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # CAMBIO: shoko_series_id pasa de INTEGER a TEXT
        conn.execute('''
            CREATE TABLE IF NOT EXISTS queue (
                id INTEGER PRIMARY KEY,
                shoko_series_id TEXT,
                anilist_id INTEGER,
                episode INTEGER,
                search_query VARCHAR,
                series_name VARCHAR,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        try:
            conn.execute("ALTER TABLE anilist_mirror ADD COLUMN repeat_count INTEGER DEFAULT 0;")
        except Exception:
            pass

