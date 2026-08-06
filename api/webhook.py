import json
import logging
from http.server import BaseHTTPRequestHandler, HTTPServer
from services.pipeline import process_webhook_payload

logger = logging.getLogger("ShokoAniSync")

class WebhookHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        content_length = int(self.headers.get('Content-Length', 0))
        post_data = self.rfile.read(content_length)

        try:
            payload = json.loads(post_data.decode('utf-8'))
            
            # Solo reaccionar ante finalizaciones de reproduccion
            if payload.get("NotificationType") != "PlaybackStop":
                self._send_response(200, "Ignored: Not PlaybackStop")
                return

            shoko_series_id = payload.get("SeriesId")
            episode = payload.get("EpisodeNumber")
            series_name = payload.get("SeriesName", "")
            item_name = payload.get("Name", "")  # Extraemos el nombre especifico (ej. Reiketsu Hen)
            
            if not shoko_series_id or not episode:
                logger.warning("[Webhook] Payload incompleto: Faltan IDs o Episodio.")
                self._send_response(400, "Missing required fields")
                return

            # Invocamos el pipeline con el nuevo campo
            process_webhook_payload(shoko_series_id, episode, series_name, item_name)
            self._send_response(200, "Event Queued for Pipeline")

        except json.JSONDecodeError:
            logger.error("[Webhook] Recibido JSON invalido.")
            self._send_response(400, "Invalid JSON")
        except Exception as e:
            logger.error("[Webhook] Fallo catastrofico en controlador: %s", str(e))
            self._send_response(500, "Internal Server Error")

    def _send_response(self, status, message):
        self.send_response(status)
        self.send_header('Content-type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps({"status": message}).encode())

    # Silenciar logs nativos HTTP para mantener consola limpia
    def log_message(self, format, *args):
        pass

def run_webhook_server(port):
    server = HTTPServer(('0.0.0.0', port), WebhookHandler)
    server.serve_forever()

