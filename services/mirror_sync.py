import time
import logging
from config import ANILIST_TOKEN
from clients.anilist import session
from database.connection import get_connection

logger = logging.getLogger("ShokoAniSync")

def sync_anilist_user_list():
    """Descarga la lista de usuario completa desde AniList y actualiza anilist_mirror en SQLite."""
    if not ANILIST_TOKEN:
        logger.warning("[CronSync] ANILIST_TOKEN no configurado. Sincronizacion omitida.")
        return False

    # 1. Obtener User ID (Handshake)
    query_user = '''query { Viewer { id name } }'''
    try:
        res_user = session.post('https://graphql.anilist.co', json={'query': query_user}, timeout=10)
        if res_user.status_code != 200:
            logger.error("[CronSync] Fallo obteniendo usuario: HTTP %s", res_user.status_code)
            return False
        
        user_data = res_user.json().get("data", {}).get("Viewer", {})
        user_id = user_data.get("id")
        logger.info("[CronSync] Usuario identificado: %s (ID: %s)", user_data.get("name"), user_id)
    except Exception as e:
        logger.error("[CronSync] Error de red en Handshake de usuario: %s", str(e))
        return False

    # 2. Descargar colecciones de AniList incluyendo el campo 'repeat'
    query_collection = '''
    query ($userId: Int) {
      MediaListCollection (userId: $userId, type: ANIME) {
        lists {
          entries {
            status
            progress
            repeat
            media {
              id
              idMal
              format
              episodes
              status
              title { romaji english }
            }
          }
        }
      }
    }
    '''

    try:
        res = session.post('https://graphql.anilist.co', json={'query': query_collection, 'variables': {'userId': user_id}}, timeout=15)
        if res.status_code != 200:
            logger.error("[CronSync] Fallo obteniendo lista: HTTP %s", res.status_code)
            return False

        data = res.json().get("data", {}).get("MediaListCollection", {}).get("lists", [])
        total_synced = 0

        with get_connection() as conn:
            for lst in data:
                for entry in lst.get("entries", []):
                    user_status = entry.get("status", "PLANNING")
                    progress = entry.get("progress", 0)
                    repeat_count = entry.get("repeat") or 0
                    
                    media = entry.get("media", {})
                    anilist_id = media.get("id")
                    mal_id = media.get("idMal")
                    fmt = media.get("format", "TV")
                    total_eps = media.get("episodes") or 999
                    media_status = media.get("status", "FINISHED")
                    
                    title_obj = media.get("title", {})
                    t_romaji = title_obj.get("romaji", "")
                    t_english = title_obj.get("english", "")

                    # BLOQUE DE INSERCIÓN / ACTUALIZACIÓN EN ESPEJO LOCAL
                    conn.execute('''
                        INSERT INTO anilist_mirror (
                            anilist_id, mal_id, title_romaji, title_english, 
                            format, status, user_status, episodes_watched, total_episodes, repeat_count
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(anilist_id) DO UPDATE SET
                            user_status = EXCLUDED.user_status,
                            episodes_watched = EXCLUDED.episodes_watched,
                            repeat_count = EXCLUDED.repeat_count,
                            last_synced = CURRENT_TIMESTAMP
                    ''', (anilist_id, mal_id, t_romaji, t_english, fmt, media_status, user_status, progress, total_eps, repeat_count))
                    
                    total_synced += 1

        logger.info("[CronSync] Espejo local actualizado exitosamente: %s obras sincronizadas.", total_synced)
        return True

    except Exception as e:
        logger.error("[CronSync] Excepcion actualizando espejo local: %s", str(e))
        return False

def daily_sync_worker():
    """Worker que ejecuta la sincronizacion en segundo plano cada 24 horas."""
    logger.info("[CronSync] Worker de sincronizacion diaria iniciado.")
    while True:
        try:
            success = sync_anilist_user_list()
            if not success:
                logger.warning("[CronSync] Sincronizacion fallida o incompleta. Reintentando en el proximo ciclo.")
        except Exception as e:
            logger.error("[CronSync] Excepcion no controlada en loop de sincronizacion: %s", str(e))
            
        # Esperar 24 horas (86400 segundos) entre sincronizaciones
        time.sleep(86400)

