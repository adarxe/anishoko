import time
import logging
import threading
from database.connection import get_connection
from services.pipeline import process_webhook_payload

logger = logging.getLogger("ShokoAniSync")

# Evento de sincronización entre hilos
task_event = threading.Event()

def notify_new_task():
    """Despierta al Conserje al instante al recibir un webhook."""
    task_event.set()

def get_and_compact_pending_queue():
    """Obtiene y consolida las tareas pendientes de la cola SQLite."""
    with get_connection() as conn:
        rows = conn.execute('''
            SELECT id, shoko_series_id, anilist_id, episode, search_query, series_name 
            FROM queue 
            ORDER BY id ASC
        ''').fetchall()

        if not rows:
            return []

        grouped_items = {}
        obsolete_queue_ids = []

        for row in rows:
            q_id, shoko_id, anilist_id, ep, query, s_name = row
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
                    obsolete_queue_ids.append(grouped_items[group_key]["queue_id"])
                    grouped_items[group_key] = {
                        "queue_id": q_id,
                        "shoko_series_id": shoko_id,
                        "anilist_id": anilist_id,
                        "episode": new_ep,
                        "search_query": query,
                        "series_name": s_name
                    }
                else:
                    obsolete_queue_ids.append(q_id)

        if obsolete_queue_ids:
            placeholders = ','.join('?' for _ in obsolete_queue_ids)
            conn.execute(f"DELETE FROM queue WHERE id IN ({placeholders})", obsolete_queue_ids)
            logger.info("[Conserje] Compactacion: %s tareas intermedias purgadas.", len(obsolete_queue_ids))

        return list(grouped_items.values())

def remove_from_queue(queue_id):
    """Elimina una tarea procesada exitosamente de la cola."""
    with get_connection() as conn:
        conn.execute("DELETE FROM queue WHERE id = ?", (queue_id,))

def offline_living_worker():
    """
    Worker demonio que procesa la cola:
    - En condiciones normales: ejecuta al instante y entra en espera indefinida (0% CPU).
    - En caso de error de red/DNS: reintenta cada 60 segundos.
    """
    logger.info("[Conserje] Worker de gestion de cola offline iniciado.")
    
    while True:
        try:
            # 1. Limpiamos la señal antes de leer SQLite para no perder eventos entrantes
            task_event.clear()
            
            # 2. Leemos la cola consolidada
            compacted_tasks = get_and_compact_pending_queue()
            all_succeeded = True

            if compacted_tasks:
                logger.info("[Conserje] Procesando %s tareas consolidadas en la cola...", len(compacted_tasks))

                for task in compacted_tasks:
                    q_id = task["queue_id"]
                    shoko_id = task["shoko_series_id"]
                    ep = task["episode"]
                    s_name = task["series_name"]
                    item_name = task.get("search_query") or s_name

                    # Intento de procesamiento en las 5 capas
                    success = process_webhook_payload(shoko_id, ep, s_name, item_name=item_name)

                    if success:
                        # Se elimina de SQLite SOLO si tuvo éxito la mutación HTTP
                        remove_from_queue(q_id)
                        logger.info("[Conserje] Tarea ID %s completada y eliminada de la cola.", q_id)
                    else:
                        all_succeeded = False
                        logger.warning("[Conserje] Tarea ID %s fallo (Red/DNS). Permanece en cola.", q_id)

                # Si falló alguna tarea por red, dormimos 60 segundos para reintentar
                if not all_succeeded:
                    logger.info("[Conserje] Falla detectada en la cola. Reintentando en 60 segundos...")
                    task_event.wait(timeout=60)
                    continue

            # Si la cola está vacía o todo se procesó con éxito:
            # Dormimos INDEFINIDAMENTE hasta que webhook.py llame a notify_new_task()
            if not compacted_tasks or all_succeeded:
                task_event.wait()

        except Exception as e:
            logger.error("[Conserje] Excepcion no controlada en bucle de cola: %s", str(e))
            time.sleep(60)

