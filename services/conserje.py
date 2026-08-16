import time
import logging
import threading
from database.connection import get_connection
from database.repository import remove_from_queue, update_queue_status 
from services.pipeline import (
    process_webhook_payload,
    STATUS_SUCCESS,
    STATUS_UNRESOLVED,
    STATUS_REQUIRES_MANUAL,
    STATUS_NETWORK_ERROR
)

logger = logging.getLogger("ShokoAniSync")

task_event = threading.Event()

def notify_new_task():
    task_event.set()

def get_and_compact_pending_queue():
    with get_connection() as conn:
        rows = conn.execute('''
            SELECT id, anidb_id, shoko_id, anilist_id, episode, search_query, series_name
            FROM queue WHERE status = 'PENDING' ORDER BY id ASC
        ''').fetchall()

        if not rows: return []

        grouped_items = {}
        obsolete_queue_ids = []

        for row in rows:
            q_id, anidb_id, shoko_id, anilist_id, ep, query, s_name = row
            group_key = f"anilist_{anilist_id}" if anilist_id and anilist_id > 0 else f"anidb_{anidb_id or s_name}"

            if group_key not in grouped_items:
                grouped_items[group_key] = {"queue_id": q_id, "anidb_id": anidb_id, "shoko_id": shoko_id, "anilist_id": anilist_id, "episode": int(ep), "search_query": query, "series_name": s_name}
            else:
                existing_ep = grouped_items[group_key]["episode"]
                new_ep = int(ep)
                if new_ep >= existing_ep:
                    obsolete_queue_ids.append(grouped_items[group_key]["queue_id"])
                    grouped_items[group_key] = {"queue_id": q_id, "anidb_id": anidb_id, "shoko_id": shoko_id, "anilist_id": anilist_id, "episode": new_ep, "search_query": query, "series_name": s_name}
                else:
                    obsolete_queue_ids.append(q_id)

        if obsolete_queue_ids:
            placeholders = ','.join('?' for _ in obsolete_queue_ids)
            conn.execute(f"DELETE FROM queue WHERE id IN ({placeholders})", obsolete_queue_ids)
            logger.info("[Conserje] Compactación: %s tareas intermedias purgadas.", len(obsolete_queue_ids))

        return list(grouped_items.values())

def offline_living_worker():
    logger.info("[Conserje] Worker de gestión de cola offline iniciado.")
    
    while True:
        try:
            task_event.clear()
            compacted_tasks = get_and_compact_pending_queue()
            all_succeeded = True

            if compacted_tasks:
                logger.info("[Conserje] Procesando %s tareas consolidadas en la cola...", len(compacted_tasks))

                for task in compacted_tasks:
                    q_id = task["queue_id"]
                    anidb_id = task["anidb_id"]
                    shoko_id = task.get("shoko_id", "")
                    ep = task["episode"]
                    s_name = task["series_name"]
                    item_name = task.get("search_query") or s_name

                    result = process_webhook_payload(anidb_id, ep, s_name, item_name=item_name, shoko_id=shoko_id)

                    if result == STATUS_SUCCESS:
                        remove_from_queue(q_id)
                        logger.info("[Conserje] Tarea ID %s completada y eliminada de la cola.", q_id)
                    elif result in (STATUS_UNRESOLVED, STATUS_REQUIRES_MANUAL):
                        update_queue_status(q_id, 'MANUAL')
                        logger.critical("[Conserje] Tarea ID %s en CUARENTENA.", q_id)
                    elif result == STATUS_NETWORK_ERROR:
                        all_succeeded = False
                        logger.warning("[Conserje] Tarea ID %s falló por red. Permanece en cola.", q_id)

                if not all_succeeded:
                    logger.info("[Conserje] Reintentando fallos en 60 segundos...")
                    task_event.wait(timeout=60)
                    continue

            if not compacted_tasks or all_succeeded:
                task_event.wait()

        except Exception as e:
            logger.error("[Conserje] Excepción en bucle de cola: %s", str(e))
            time.sleep(60)

