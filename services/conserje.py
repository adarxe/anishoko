import time
import logging
import threading
from database.connection import get_connection
from services.pipeline import process_webhook_payload
from services.pipeline import (
    process_webhook_payload,
    STATUS_SUCCESS,
    STATUS_UNRESOLVED,
    STATUS_NETWORK_ERROR
)

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
    """Worker demonio que procesa y limpia la cola según el resultado real."""
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
                    shoko_id = task["shoko_series_id"]
                    ep = task["episode"]
                    s_name = task["series_name"]
                    item_name = task.get("search_query") or s_name

                    # Evaluamos resultado
                    result = process_webhook_payload(shoko_id, ep, s_name, item_name=item_name)

                    if result == STATUS_SUCCESS:
                        remove_from_queue(q_id)
                        logger.info("[Conserje] Tarea ID %s completada y eliminada de la cola.", q_id)
                        
                    elif result == STATUS_UNRESOLVED:
                        # Título no localizado: Se borra para evitar bucle infinito y logs falsos
                        remove_from_queue(q_id)
                        logger.warning("[Conserje] Tarea ID %s descartada: No se encontró id en AniList.", q_id)
                        
                    elif result == STATUS_NETWORK_ERROR:
                        # Error de red/DNS real: Se conserva en cola para reintentar
                        all_succeeded = False
                        logger.warning("[Conserje] Tarea ID %s falló por red/conectividad. Permanece en cola.", q_id)

                if not all_succeeded:
                    logger.info("[Conserje] Fallos de red detectados. Reintentando en 60 segundos...")
                    task_event.wait(timeout=60)
                    continue

            if not compacted_tasks or all_succeeded:
                task_event.wait()

        except Exception as e:
            logger.error("[Conserje] Excepción no controlada en bucle de cola: %s", str(e))
            time.sleep(60)
