import time
import logging
from config import ANILIST_TOKEN
from clients.anilist import session
from database.connection import get_connection

logger = logging.getLogger("ShokoAniSync")

def sync_anilist_user_list():
    """
    Sincronización de lista de usuario con jerarquía SoT:
    - AniList API: Fuente de Verdad para EXISTENCIA (Adición/Eliminación de entradas).
    - Mirror Local: Fuente de Verdad para PROGRESO Y ESTADO (Impide retrocesos).
    """
    if not ANILIST_TOKEN:
        logger.warning("[CronSync] ANILIST_TOKEN no configurado. Sincronización omitida.")
        return False

    # 1. Handshake de Usuario
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

    # 2. Descargar colecciones GraphQL
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
        
        remote_ids = set()
        total_synced = 0

        with get_connection() as conn:
            # Identificar obras congeladas por tareas pendientes en la cola offline
            pending_queue_ids = {
                row[0] for row in conn.execute("SELECT DISTINCT anilist_id FROM queue WHERE anilist_id > 0").fetchall()
            }

            for lst in data:
                for entry in lst.get("entries", []):
                    user_status = entry.get("status", "PLANNING")
                    progress = entry.get("progress", 0)
                    repeat_count = entry.get("repeat") or 0
                    
                    media = entry.get("media", {})
                    anilist_id = media.get("id")
                    if not anilist_id:
                        continue

                    remote_ids.add(anilist_id)
                    mal_id = media.get("idMal")
                    fmt = media.get("format", "TV")
                    total_eps = media.get("episodes") or 999
                    media_status = media.get("status", "FINISHED")
                    
                    title_obj = media.get("title", {})
                    t_romaji = title_obj.get("romaji", "")
                    t_english = title_obj.get("english", "")

                    # Consultar estado actual en el espejo local
                    row = conn.execute(
                        "SELECT episodes_watched, user_status, repeat_count FROM anilist_mirror WHERE anilist_id = ?",
                        (anilist_id,)
                    ).fetchone()

                    if row is None:
                        # ENTRADA NUEVA (Dictaminada por AniList API)
                        conn.execute('''
                            INSERT INTO anilist_mirror (
                                anilist_id, mal_id, title_romaji, title_english, 
                                format, status, user_status, episodes_watched, total_episodes, repeat_count
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ''', (anilist_id, mal_id, t_romaji, t_english, fmt, media_status, user_status, progress, total_eps, repeat_count))
                    else:
                        local_eps, local_status, local_repeat = row
                        
                        # REGLA SoT: Si no hay tareas pendientes en cola, aplicar protección de progreso
                        if anilist_id not in pending_queue_ids:
                            final_progress = max(progress, local_eps)
                            # Si el progreso local es superior, retenemos el estado local
                            final_status = user_status if progress > local_eps else local_status
                            final_repeat = repeat_count if progress > local_eps else local_repeat

                            conn.execute('''
                                UPDATE anilist_mirror SET
                                    mal_id = ?,
                                    title_romaji = ?,
                                    title_english = ?,
                                    format = ?,
                                    status = ?,
                                    user_status = ?,
                                    episodes_watched = ?,
                                    total_episodes = ?,
                                    repeat_count = ?,
                                    last_synced = CURRENT_TIMESTAMP
                                WHERE anilist_id = ?
                            ''', (mal_id, t_romaji, t_english, fmt, media_status, final_status, final_progress, total_eps, final_repeat, anilist_id))

                    total_synced += 1

            # 3. PURGA DE HUÉRFANOS (AniList dictamina eliminaciones)
            if remote_ids:
                placeholders = ','.join('?' for _ in remote_ids)
                cursor = conn.execute(f"DELETE FROM anilist_mirror WHERE anilist_id NOT IN ({placeholders})", list(remote_ids))
                if cursor.rowcount > 0:
                    logger.info("[CronSync] Purga de huérfanos: %s entradas eliminadas localmente.", cursor.rowcount)

        logger.info("[CronSync] Espejo local actualizado exitosamente: %s obras procesadas.", total_synced)
        return True

    except Exception as e:
        logger.error("[CronSync] Excepción actualizando espejo local: %s", str(e))
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

