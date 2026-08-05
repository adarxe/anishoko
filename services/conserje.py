import time
import logging
from database.repository import get_queue, remove_from_queue
from clients.anilist import post_to_anilist, resolve_title_smart

logger = logging.getLogger("ShokoAniSync")

def offline_living_worker():
    """Worker de segundo plano que procesa webhooks retenidos en SQLite/DuckDB."""
    logger.info("[Worker] Hilo Conserje iniciado. Monitorizando cola offline...")
    while True:
        try:
            queue_items = get_queue()
            for item in queue_items:
                target_anilist_id = item["anilist_id"]

                # Si el ID nunca se resolvió, intentarlo de nuevo (Capa 3 de emergencia)
                if not target_anilist_id or target_anilist_id == 0:
                    logger.info("[Worker] Resolviendo ID diferido para '%s'...", item["series_name"])
                    resolved_id, _ = resolve_title_smart(item["series_name"])
                    if resolved_id:
                        target_anilist_id = resolved_id
                    else:
                        continue

                success = post_to_anilist(target_anilist_id, item["episode"])
                if success:
                    remove_from_queue(item["id"])
                    logger.info("[Worker] Tarea encolada finalizada y purgada.")

                time.sleep(1.5)
        except Exception as e:
            logger.error("[Worker] Error en el ciclo del conserje: %s", str(e))
        
        # Duerme 60 segundos si la cola está vacía para no ahogar la CPU
        time.sleep(60)

