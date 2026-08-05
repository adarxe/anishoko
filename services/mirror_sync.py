import time
import subprocess
import sys
import logging
from config import SYNC_SCRIPT_NAME

logger = logging.getLogger("ShokoAniSync")

def run_sync_script(tipo_ejecucion):
    """Ejecuta de manera segura el clonado de tu cuenta de AniList."""
    logger.info("[CronSync] Iniciando proceso de clonacion (%s)", tipo_ejecucion)
    try:
        result = subprocess.run(
            [sys.executable, SYNC_SCRIPT_NAME], 
            capture_output=True, 
            text=True, 
            timeout=45
        )
        if result.returncode == 0:
            logger.info("[CronSync] Espejado de base de datos finalizado correctamente.")
        else:
            logger.warning("[CronSync] Subproceso omitido. Operando con caché existente.")
    except Exception as e:
        logger.warning("[CronSync] Subproceso interrumpido por estado offline: %s", str(e))

def daily_sync_worker():
    """Hilo demonio que ejecuta el espejo al iniciar y luego cada 24H."""
    logger.info("[CronSync] Demonio de sincronizacion programada activado.")
    run_sync_script("Inicial (Boot)")
    while True:
        time.sleep(86400)
        run_sync_script("Ciclo 24H")

