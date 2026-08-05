import requests
import logging
from config import SHOKO_URL, SHOKO_API_KEY

logger = logging.getLogger("ShokoAniSync")

def fetch_anilist_id_from_shoko(shoko_series_id):
    """Consulta la API de Shoko para obtener el AniList ID o MAL ID asociado a una serie."""
    headers = {
        'Accept': 'application/json',
        'apikey': SHOKO_API_KEY
    }
    url = f"{SHOKO_URL}/api/v3/Series/{shoko_series_id}/AniDB"
    try:
        res = requests.get(url, headers=headers, timeout=5)
        if res.status_code == 200:
            data = res.json()
            return data.get("AniListID") or data.get("MALID")
    except requests.exceptions.RequestException as e:
        logger.error("[ShokoClient] Error consultando Shoko API: %s", str(e))
    return None

