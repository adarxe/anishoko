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
    Consulta Shoko Series ID. 
    Si la obra contiene múltiples MAL IDs (ej. sagas de películas bajo un solo AniDB ID),
    retorna None para forzar desambiguación precisa por título/episodio en las capas superiores.
    """
    if not SHOKO_URL or not shoko_id:
        return None

    url = f"{SHOKO_URL.rstrip('/')}/api/v3/Series/{shoko_id}"

    try:
        response = session.get(url, timeout=1.5)
        if response.status_code == 200:
            data = response.json()
            mal_ids = data.get("IDs", {}).get("MAL", [])
            
            # PROTECCIÓN ANTICORRUPCIÓN:
            # Si hay exactamente 1 MAL ID, es seguro (serie estándar).
            if len(mal_ids) == 1:
                logger.info("[ShokoBridge] Éxito unívoco vía Shoko_ID %s -> MAL ID %s", shoko_id, mal_ids[0])
                return mal_ids[0]
            
            # Si hay múltiples MAL IDs (ej. Kara no Kyoukai), rechazamos para desambiguar por episodio
            if len(mal_ids) > 1:
                logger.warning("[ShokoBridge] Ambigüedad detectada en Shoko_ID %s (%s MAL IDs). Pasando a desambiguación por episodio.", shoko_id, len(mal_ids))
                return None
                
    except requests.exceptions.RequestException:
        logger.info("[ShokoBridge] Shoko Server no disponible (0 ms fallback).")
    
    return None
