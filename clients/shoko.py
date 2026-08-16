import requests
import logging
from requests.adapters import HTTPAdapter
from config import SHOKO_URL, SHOKO_API_KEY

logger = logging.getLogger("ShokoAniSync")

# Sesión HTTP estricta: 0 reintentos para fallar a 0ms si Shoko está apagado
session = requests.Session()
session.mount("http://", HTTPAdapter(max_retries=0))
session.mount("https://", HTTPAdapter(max_retries=0))

session.headers.update({
    'Accept': 'application/json',
    'apikey': SHOKO_API_KEY
})

def fetch_mal_id_from_shoko(shoko_id):
    """
    Consulta Shoko usando únicamente el Shoko Series ID sin reintentos.
    Falla en 1ms si el servidor no está activo.
    """
    if not SHOKO_URL or not shoko_id:
        return None

    url = f"{SHOKO_URL.rstrip('/')}/api/v3/Series/{shoko_id}"

    try:
        response = session.get(url, timeout=1.5)
        if response.status_code == 200:
            data = response.json()
            mal_id = data.get("IDs", {}).get("MAL", [None])[0] or data.get("MalID")
            if mal_id:
                logger.info("[ShokoBridge] Éxito vía Shoko_ID %s -> MAL ID %s", shoko_id, mal_id)
                return mal_id
            else:
                logger.info("[ShokoBridge] Serie %s encontrada en Shoko pero sin mapeo a MAL.", shoko_id)
        else:
            logger.warning("[ShokoBridge] HTTP %s desde endpoint: %s", response.status_code, url)
    except requests.exceptions.RequestException:
        logger.info("[ShokoBridge] Shoko Server no disponible en %s (0 ms fallback).", url)
    
    return None

