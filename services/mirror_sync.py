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

    # 1. Obtener el ID del usuario autenticado (Viewer)
    viewer_query = '''
    query {
      Viewer {
        id
        name
      }
    }
    '''
    
    try:
        res_viewer = session.post('https://graphql.anilist.co', json={'query': viewer_query}, timeout=10)
        if res_viewer.status_code != 200:
            logger.error("[CronSync] Error obteniendo Viewer (HTTP %s): %s", res_viewer.status_code, res_viewer.text)
            return False
            
        viewer_data = res_viewer.json().get("data", {}).get("Viewer", {})
        user_id = viewer_data.get("id")
        
        if not user_id:
            logger.error("[CronSync] No se pudo extraer el userId de la respuesta de AniList.")
            return False
            
        logger.info("[CronSync] Usuario identificado: %s (ID: %s)", viewer_data.get("name"), user_id)

    except Exception as e:
        logger.error("[CronSync] Fallo de red consultando Viewer: %s", str(e))
        return False

    # 2. Consultar la coleccion pasando el userId requerido por la API
    collection_query = '''
    query ($userId: Int) {
      MediaListCollection (userId: $userId, type: ANIME, status_in: [CURRENT, PLANNING, COMPLETED, DROPPED, PAUSED]) {
        lists {
          entries {
            status
            progress
            media {
              id
              idMal
              format
              episodes
              status
              title {
                romaji
                english
              }
            }
          }
        }
      }
    }
    '''

    try:
        logger.info("[CronSync] Descargando colecciones de usuario desde AniList...")
        res = session.post(
            'https://graphql.anilist.co', 
            json={'query': collection_query, 'variables': {'userId': user_id}}, 
            timeout=15
        )
        
        if res.status_code != 200:
            logger.error("[CronSync] Fallo en MediaListCollection (HTTP %s): %s", res.status_code, res.text)
            return False

        data = res.json().get("data", {}).get("MediaListCollection", {})
        lists = data.get("lists", [])

        if not lists:
            logger.warning("[CronSync] No se encontraron listas de anime en la cuenta.")
            return False

        total_entries = 0
        with get_connection() as conn:
            for l in lists:
                for entry in l.get("entries", []):
                    media = entry.get("media", {})
                    anilist_id = media.get("id")
                    if not anilist_id:
                        continue

                    user_status = entry.get("status")
                    episodes_watched = entry.get("progress", 0)
                    mal_id = media.get("idMal")
                    format_type = media.get("format", "TV")
                    total_episodes = media.get("episodes") or 999
                    status = media.get("status", "FINISHED")
                    t_romaji = media.get("title", {}).get("romaji", "")
                    t_english = media.get("title", {}).get("english", "")

                    conn.execute('''
                        INSERT INTO anilist_mirror (
                            anilist_id, mal_id, title_romaji, title_english, 
                            format, status, user_status, episodes_watched, total_episodes, last_synced
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                        ON CONFLICT(anilist_id) DO UPDATE SET
                            user_status = excluded.user_status,
                            episodes_watched = excluded.episodes_watched,
                            total_episodes = excluded.total_episodes,
                            last_synced = CURRENT_TIMESTAMP
                    ''', (anilist_id, mal_id, t_romaji, t_english, format_type, status, user_status, episodes_watched, total_episodes))
                    
                    total_entries += 1

        logger.info("[CronSync] Espejo local actualizado exitosamente: %s obras sincronizadas.", total_entries)
        return True

    except Exception as e:
        logger.error("[CronSync] Error durante la sincronizacion nativa: %s", str(e))
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

