import os
import logging
from pathlib import Path
from dotenv import load_dotenv

# Base Directory usando pathlib para portabilidad absoluta
BASE_DIR = Path(__file__).resolve().parent

# Carga forzada del .env desde la raíz del proyecto
ENV_PATH = BASE_DIR / ".env"
load_dotenv(dotenv_path=ENV_PATH)

# Variables de Configuración Global
ANILIST_TOKEN = os.getenv("ANILIST_TOKEN", "").strip()
SHOKO_URL = os.getenv("SHOKO_URL", "http://localhost:8111").rstrip('/')
SHOKO_API_KEY = os.getenv("SHOKO_API_KEY", "").strip()
PORT = int(os.getenv("PORT", "5001"))

# Archivos de datos
DB_FILE = BASE_DIR / "shoko_sync.duckdb"
SYNC_SCRIPT_NAME = "fetch_anilist_mirror.py"

# Configuración centralizada de Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | [%(levelname)s] | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("ShokoAniSync")

