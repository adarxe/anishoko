import requests
import logging
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from config import SHOKO_URL, SHOKO_API_KEY

logger = logging.getLogger("ShokoAniSync")

session = requests.Session()
retry_strategy = Retry(
    total=3,
    backoff_factor=0.5,
    status_forcelist=[500, 502, 503, 504],
    allowed_methods=["GET"]
)
session.mount("http://", HTTPAdapter(max_retries=retry_strategy))
session.mount("https://", HTTPAdapter(max_retries=retry_strategy))

session.headers.update({
    'Accept': 'application/json',
    'apikey': SHOKO_API_KEY
})

def fetch_anilist_id_from_shoko(shoko_series_id):
    """Consulta la API de Shoko para obtener el AniList ID o MAL ID asociado a una serie."""
    url = f"{SHOKO_URL}/api/v3/Series/{shoko_series_id}/AniDB"
    try:
        res = session.get(url, timeout=5)
        if res.status_code == 200:
            data = res.json()
            return data.get("AniListID") or data.get("MALID")
    except requests.exceptions.RequestException as e:
        logger.error("[ShokoClient] Error consultando Shoko API (ID: %s): %s", shoko_series_id, str(e))
    return None
