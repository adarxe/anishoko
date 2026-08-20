import sqlite3
import logging
from config import DB_PATH

logger = logging.getLogger("ShokoAniSync")

def get_connection():
    conn = sqlite3.connect(DB_PATH, timeout=20.0)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn

def init_db():
    """Inicializa la estructura de tablas usando anidb_id como Llave Maestra."""
    try:
        with get_connection() as conn:
            conn.executescript('''
                CREATE TABLE IF NOT EXISTS series_mapping (
                    anidb_id TEXT NOT NULL,
                    episode INTEGER NOT NULL,
                    anilist_id INTEGER NOT NULL,
                    search_query TEXT,
                    romaji_name TEXT,
                    is_ambiguous INTEGER DEFAULT 0,
                    last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (anidb_id, episode)
                );

                CREATE TABLE IF NOT EXISTS queue (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    anidb_id TEXT NOT NULL,
                    shoko_id TEXT DEFAULT '',
                    anilist_id INTEGER DEFAULT 0,
                    episode INTEGER NOT NULL,
                    search_query TEXT,
                    series_name TEXT,
                    status TEXT DEFAULT 'PENDING',
                    attempts INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS watch_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    anidb_id TEXT NOT NULL,
                    episode INTEGER NOT NULL,
                    anilist_id INTEGER NOT NULL,
                    series_name TEXT,
                    watched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS anilist_mirror (
                    anilist_id INTEGER PRIMARY KEY,
                    mal_id INTEGER,
                    title_romaji TEXT,
                    title_english TEXT,
                    format TEXT,
                    status TEXT,
                    user_status TEXT,
                    episodes_watched INTEGER DEFAULT 0,
                    total_episodes INTEGER DEFAULT 0,
                    repeat_count INTEGER DEFAULT 0,
                    last_synced TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS relations_cache (
                    seed_id INTEGER PRIMARY KEY,
                    relations_json TEXT NOT NULL,
                    cached_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            ''')
        logger.info("[System] Base de datos inicializada correctamente.")
    except Exception as e:
        logger.critical("[System] Error fatal durante la inicialización de la base de datos: %s", str(e))
        raise e
