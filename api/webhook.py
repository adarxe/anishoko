import json
import logging
from http.server import BaseHTTPRequestHandler, HTTPServer
from services.pipeline import process_webhook_payload

logger = logging.getLogger("ShokoAniSync")

MIN_PLAYBACK_PERCENTAGE = 85.0

class WebhookHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        content_length = int(self.headers.get('Content-Length', 0))
        post_data = self.rfile.read(content_length)

        try:
            payload = json.loads(post_data.decode('utf-8'))
            
            # ----------------------------------------------------
            # FILTRO 1: Tipo de evento
            # ----------------------------------------------------
            if payload.get("NotificationType") != "PlaybackStop":
                self._send_response(200, "Ignored: Not PlaybackStop")
                return

            # ----------------------------------------------------
            # FILTRO 2: Detección de Contenido Occidental (Non-Anime)
            # ----------------------------------------------------
            provider_ids = payload.get("ProviderIds", {})
            shoko_series_id = payload.get("SeriesId") or provider_ids.get("Shoko Series") or provider_ids.get("Shoko")

            # Si contiene identificadores explícitos occidentales o carece del ID de Shoko, es occidental
            has_western_provider = any(k in provider_ids for k in ["Imdb", "Tvdb", "Tmdb", "IMDb", "TVDb", "TMDb"])
            
            if not shoko_series_id or (has_western_provider and not shoko_series_id):
                logger.info("[Webhook] Evento descartado: Contenido no gestionado por Shoko (Serie occidental / Pelicula no-anime)")
                self._send_response(200, "Ignored: Non-Shoko content")
                return

            # ----------------------------------------------------
            # FILTRO 3: Umbral de reproducción (Porcentaje)
            # ----------------------------------------------------
            played_to_completion = payload.get("PlayedToCompletion", False)
            position_ticks = payload.get("PlaybackPositionTicks") or payload.get("PositionTicks") or 0
            runtime_ticks = payload.get("RunTimeTicks") or 0
            
            played_pct = (position_ticks / runtime_ticks * 100.0) if runtime_ticks > 0 else 0.0

            if not played_to_completion and played_pct < MIN_PLAYBACK_PERCENTAGE:
                logger.info(
                    "[Webhook] Evento omitido: Reproduccion incompleta (Completado: %s | Progreso: %.1f%%)", 
                    played_to_completion, played_pct
                )
                self._send_response(200, "Ignored: Playback threshold not met")
                return

            # ----------------------------------------------------
            # EXTRACCIÓN DE DATOS Y ENVÍO AL PIPELINE
            # ----------------------------------------------------
            episode = payload.get("EpisodeNumber")
            series_name = payload.get("SeriesName", "")
            item_name = payload.get("Name", "")

            if not episode:
                logger.warning("[Webhook] Payload incompleto: Falta numero de episodio.")
                self._send_response(400, "Missing episode number")
                return

            logger.info("[Webhook] Evento validado: '%s' (Ep: %s | Progreso: %.1f%%)", series_name, episode, played_pct)
            process_webhook_payload(shoko_series_id, episode, series_name, item_name)
            self._send_response(200, "Event Queued for Pipeline")

        except json.JSONDecodeError:
            logger.error("[Webhook] Recibido JSON invalido.")
            self._send_response(400, "Invalid JSON")
        except Exception as e:
            logger.error("[Webhook] Fallo en controlador webhook: %s", str(e))
            self._send_response(500, "Internal Server Error")

    def _send_response(self, status, message):
        self.send_response(status)
        self.send_header('Content-type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps({"status": message}).encode())

    def log_message(self, format, *args):
        pass

def run_webhook_server(port):
    server = HTTPServer(('0.0.0.0', port), WebhookHandler)
    server.serve_forever()

