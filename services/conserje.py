import time
import logging
from database.connection import get_connection
from services.pipeline import process_webhook_payload

logger = logging.getLogger("ShokoAniSync")

def get_and_compact_pending_queue():
    """
    Obtiene las tareas pendientes de la cola SQLite y las compacta por serie,
    conservando unicamente el episodio mas alto por cada anime.
    """
    with get_connection() as conn:
        # Extraer todas las tareas pendientes ordenadas por ID de insercion
        rows = conn.execute('''
            SELECT id, shoko_series_id, anilist_id, episode, search_query, series_name 
            FROM queue 
            ORDER BY id ASC
        ''').fetchall()

        if not rows:
            return []

        # Diccionario para agrupar por entidad: key -> mejor_item
        grouped_items = {}
        obsolete_queue_ids = []

        for row in rows:
            q_id, shoko_id, anilist_id, ep, query, s_name = row
            
            # Definir clave unica de agrupacion (AniList ID si existe, o Shoko ID / Título)
            group_key = f"anilist_{anilist_id}" if anilist_id and anilist_id > 0 else f"shoko_{shoko_id or s_name}"

            if group_key not in grouped_items:
                grouped_items[group_key] = {
                    "queue_id": q_id,
                    "shoko_series_id": shoko_id,
                    "anilist_id": anilist_id,
                    "episode": int(ep),
                    "search_query": query,
                    "series_name": s_name
                }
            else:
                existing_ep = grouped_items[group_key]["episode"]
                new_ep = int(ep)

                if new_ep >= existing_ep:
                    # El nuevo registro es igual o mayor: el registro anterior en cola queda obsoleto
                    obsolete_queue_ids.append(grouped_items[group_key]["queue_id"])
                    
                    # Reemplazamos por el registro mas reciente/alto
                    grouped_items[group_key] = {
                        "queue_id": q_id,
                        "shoko_series_id": shoko_id,
                        "anilist_id": anilist_id,
                        "episode": new_ep,
                        "search_query": query,
                        "series_name": s_name
                    }
                else:
                    # El registro nuevo es menor a uno procesado previamente: se marca como obsoleto
                    obsolete_queue_ids.append(q_id)

        # Limpiar inmediatamente de SQLite los registros intermedios obsoletos
        if obsolete_queue_ids:
            placeholders = ','.join('?' for _ in obsolete_queue_ids)
            conn.execute(f"DELETE FROM queue WHERE id IN ({placeholders})", obsolete_queue_ids)
            logger.info("[Conserje] Compactacion de cola: %s tareas intermedias purgadas.", len(obsolete_queue_ids))

        return list(grouped_items.values())


def remove_from_queue(queue_id):
    """Elimina una tarea procesada exitosamente de la cola."""
    with get_connection() as conn:
        conn.execute("DELETE FROM queue WHERE id = ?", (queue_id,))


def offline_living_worker():
    """
    Worker demonio que procesa la cola offline periódicamente cuando hay conexión disponible.
    """
    logger.info("[Conserje] Worker de gestion de cola offline iniciado.")
    
    while True:
        try:
            compacted_tasks = get_and_compact_pending_queue()

            if compacted_tasks:
                logger.info("[Conserje] Procesando %s tareas consolidadas en la cola offline...", len(compacted_tasks))

                for task in compacted_tasks:
                    q_id = task["queue_id"]
                    shoko_id = task["shoko_series_id"]
                    ep = task["episode"]
                    s_name = task["series_name"]

                    # Intentamos procesar a traves de la tuberia principal
                    success = process_webhook_payload(shoko_id, ep, s_name, item_name=s_name)

                    if success:
                        remove_from_queue(q_id)
                        logger.info("[Conserje] Tarea ID %s completada y eliminada de la cola.", q_id)
                    else:
                        logger.warning("[Conserje] Tarea ID %s fallo en esta ejecucion. Se mantendra para el proximo ciclo.", q_id)

        except Exception as e:
            logger.error("[Conserje] Excepcion no controlada en bucle de cola offline: %s", str(e))

        # Reintentar cada 5 minutos (300 segundos)
        time.sleep(300)
