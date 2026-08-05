import duckdb
import logging
from config import DB_FILE

logger = logging.getLogger("ShokoAniSync")

def get_connection():
    """Retorna una conexión a la base de datos DuckDB."""
    return duckdb.connect(str(DB_FILE))

def init_db():
    """Inicializa todas las tablas y realiza migraciones de columnas si no existen."""
    with get_connection() as conn:
        # 1. Tabla Mirror
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
                last_synced TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_watched_at TIMESTAMP
            )
        ''')

        # 2. Tabla Mapeo (Caché L1)
        conn.execute('''
            CREATE TABLE IF NOT EXISTS series_mapping (
                shoko_series_id INTEGER,
                episode INTEGER,
                anilist_id INTEGER,
                search_query VARCHAR,
                romaji_name VARCHAR,
                last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_watched_at TIMESTAMP,
                PRIMARY KEY (shoko_series_id, episode)
            )
        ''')

        # 3. Tabla Caché de Relaciones de Franquicias (Caché BFS)
        conn.execute('''
            CREATE TABLE IF NOT EXISTS relations_cache (
                base_anilist_id INTEGER PRIMARY KEY,
                related_ids_json VARCHAR,
                cached_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # 4. Tabla Cola de Eventos Pendientes (Offline)
        conn.execute('''
            CREATE TABLE IF NOT EXISTS queue (
                id INTEGER PRIMARY KEY,
                shoko_series_id INTEGER,
                anilist_id INTEGER,
                episode INTEGER,
                search_query VARCHAR,
                series_name VARCHAR,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # Migraciones preventivas para bases de datos existentes
        for table, col in [("anilist_mirror", "last_watched_at"), ("series_mapping", "last_watched_at")]:
            try:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} TIMESTAMP")
            except Exception:
                pass

