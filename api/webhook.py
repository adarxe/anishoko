import re
import json
import logging
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from database.repository import add_to_queue
from services.conserje import notify_new_task

logger = logging.getLogger("ShokoAniSync")

MIN_PLAYBACK_PERCENTAGE = 85.0

class WebhookHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        content_length = int(self.headers.get('Content-Length', 0))
        post_data = self.rfile.read(content_length)

        try:
            payload = json.loads(post_data.decode('utf-8'))

            if payload.get("NotificationType") != "PlaybackStop":
                self._send_response(200, "Ignored: Not PlaybackStop")
                return

            provider_ids = payload.get("ProviderIds", {})
            provider_custom = payload.get("Provider_custom", "")

            anidb_id = payload.get("Provider_anidb") or provider_ids.get("Anidb") or provider_ids.get("AniDB")

            shoko_id = (
                payload.get("Provider_shoko series") or
                payload.get("Provider_shoko_series") or
                provider_ids.get("Shoko Series") or
                provider_ids.get("Shoko")
            )

            if not shoko_id and provider_custom:
                match = re.search(r'seriesId=(\d+)', provider_custom)
                if match:
                    shoko_id = match.group(1)

            has_shoko_marker = bool(shoko_id or re.search(r'seriesId=\d+', provider_custom) or provider_ids.get("Shoko Series") or provider_ids.get("Shoko"))
            has_western_provider = any(k in provider_ids for k in ["Imdb", "Tvdb", "Tmdb", "IMDb", "TVDb", "TMDb"])

            if not anidb_id or (has_western_provider and not has_shoko_marker):
                logger.info("[Webhook] Evento omitido: Contenido no gestionado por AniDB/Shoko.")
                self._send_response(200, "Ignored: Non-AniDB content")
                return

            played_to_completion = payload.get("PlayedToCompletion", False)
            position_ticks = payload.get("PlaybackPositionTicks") or payload.get("PositionTicks") or 0
            runtime_ticks = payload.get("RunTimeTicks") or 0
            played_pct = (position_ticks / runtime_ticks * 100.0) if runtime_ticks > 0 else 0.0
            series_name = payload.get("SeriesName", "Desconocido")

            if not played_to_completion and played_pct < MIN_PLAYBACK_PERCENTAGE:
                logger.info("[Webhook] Evento omitido: Umbral incompleto (%.1f%%) en '%s'", played_pct, series_name)
                self._send_response(200, "Ignored: Playback threshold not met")
                return

            episode = payload.get("EpisodeNumber")
            item_name = payload.get("Name", "") or series_name

            if episode is None:
                logger.warning("[Webhook] Payload descartado: Falta número de episodio en '%s'.", series_name)
                self._send_response(400, "Missing episode number")
                return

            add_to_queue(anidb_id, 0, episode, search_query=item_name, series_name=series_name, shoko_id=shoko_id)
            logger.info("[Webhook] Evento encolado: '%s' (AniDB: %s | Shoko: %s | Ep: %s | Progreso: %.1f%%)", 
                        series_name, anidb_id, shoko_id or "N/A", episode, played_pct)

            notify_new_task()
            self._send_response(200, "Event Queued Successfully")

        except json.JSONDecodeError:
            self._send_response(400, "Invalid JSON")
        except Exception as e:
            logger.error("[Webhook] Error crítico: %s", str(e), exc_info=True)
            self._send_response(500, "Internal Server Error")

    def _send_response(self, status, message):
        self.send_response(status)
        self.send_header('Content-type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps({"status": message}).encode())

    def log_message(self, format, *args):
        pass

def run_webhook_server(port):
    server = ThreadingHTTPServer(('0.0.0.0', port), WebhookHandler)
    server.serve_forever()

