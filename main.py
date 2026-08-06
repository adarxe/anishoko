import threading
import logging
from config import PORT
from database.connection import init_db
from services.conserje import offline_living_worker
from services.mirror_sync import daily_sync_worker
from api.webhook import run_webhook_server

logger = logging.getLogger("ShokoAniSync")

if __name__ == '__main__':
    logger.info("[System] Booting Shoko-AniList Sync Daemon (Clean Architecture)...")

    # 1. Inicializar la base de datos SQLite y migraciones
    try:
        init_db()
        logger.info("[System] Database subsystem initialized successfully.")
    except Exception as e:
        logger.critical("[System] Fatal error during database initialization: %s", str(e))
        exit(1)

    # 2. Iniciar Worker de Sincronizacion Diaria (Espejo de AniList)
    logger.info("[System] Spawning Daily Sync Worker thread.")
    threading.Thread(target=daily_sync_worker, daemon=True).start()

    # 3. Iniciar Worker Conserje (Gestion de Cola Offline/Reintentos)
    logger.info("[System] Spawning Offline Queue Manager (Conserje) thread.")
    threading.Thread(target=offline_living_worker, daemon=True).start()

    # 4. Iniciar Servidor Webhook Multihilo (Bloqueante)
    try:
        logger.info("[System] HTTP Daemon transitioning to listening state on port %s.", PORT)
        run_webhook_server(PORT)
    except KeyboardInterrupt:
        logger.info("[System] Interrupt signal received (SIGINT). Initiating graceful shutdown.")
    except Exception as e:
        logger.error("[System] Unexpected fatal error in HTTP Daemon: %s", str(e))
    finally:
        logger.info("[System] Daemon shutdown sequence complete.")

