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
    """
    Retorna las tareas en orden cronológico (FIFO).
    La compactación fue desactivada para preservar el historial individual
    de cada episodio en el timeline de AniList tras recuperar la conexión.
    """
    with get_connection() as conn:
        rows = conn.execute('''
            SELECT id, anidb_id, shoko_id, anilist_id, episode, search_query, series_name
            FROM queue WHERE status = 'PENDING' ORDER BY id ASC
        ''').fetchall()

        if not rows: return []

        tasks = []
        for row in rows:
            q_id, anidb_id, shoko_id, anilist_id, ep, query, s_name = row
            tasks.append({
                "queue_id": q_id, 
                "anidb_id": anidb_id, 
                "shoko_id": shoko_id, 
                "anilist_id": anilist_id, 
                "episode": int(ep), 
                "search_query": query, 
                "series_name": s_name
            })

        return tasks

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

