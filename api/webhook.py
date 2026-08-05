import json
import logging
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from services.pipeline import process_webhook_payload

logger = logging.getLogger("ShokoAniSync")

class JellyfinWebhookHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass  # Silenciar los molestos logs HTTP por defecto

    def do_POST(self):
        content_length = int(self.headers.get('Content-Length', 0))
        post_data = self.rfile.read(content_length)

        try:
            payload = json.loads(post_data.decode('utf-8'))
        except json.JSONDecodeError:
            self.send_response(400)
            self.end_headers()
            return

        # Buena práctica: Responder 200 OK a Jellyfin inmediatamente
        self.send_response(200)
        self.send_header('Content-Type', 'text/plain')
        self.end_headers()
        self.wfile.write(b"OK")

        # Extraer variables esenciales
        series_name = payload.get("SeriesName")
        episode = payload.get("EpisodeNumber")
        shoko_series_id = payload.get("SeriesId")

        if not all([series_name, episode, shoko_series_id]):
            logger.warning("[Webhook] Payload ignorado por falta de datos.")
            return

        logger.info("[Webhook] Evento recibido: '%s' (Episodio: %s | Shoko ID: %s)", series_name, episode, shoko_series_id)

        # Enviar al motor de 5 capas (Pipeline)
        process_webhook_payload(shoko_series_id, episode, series_name)

def run_webhook_server(port):
    """Inicializa el servidor multihilo en el puerto especificado."""
    server_address = ('', port)
    httpd = ThreadingHTTPServer(server_address, JellyfinWebhookHandler)
    logger.info("[Daemon] Servidor local operando sobre puerto %s.", port)
    httpd.serve_forever()

