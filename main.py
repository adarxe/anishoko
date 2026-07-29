import os
import json
import time
import threading
import requests
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from dotenv import load_dotenv
from pathlib import Path

import db_manager

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

PORT = 5000
ANILIST_TOKEN = os.getenv("ANILIST_TOKEN")
SHOKO_HOST = os.getenv("SHOKO_HOST", "127.0.0.1")
SHOKO_PORT = os.getenv("SHOKO_PORT", "8111")
SHOKO_API_KEY = os.getenv("SHOKO_API_KEY")

db_manager.init_db()

def post_to_anilist(anilist_id, episode):
    """Envía el progreso del episodio directamente a la API de AniList."""
    query = '''
    mutation ($mediaId: Int, $progress: Int, $status: MediaListStatus) {
        SaveMediaListEntry (mediaId: $mediaId, progress: $progress, status: $status) {
            id
            progress
        }
    }
    '''
    variables = {'mediaId': anilist_id, 'progress': episode, 'status': 'CURRENT'}
    headers = {
        'Authorization': f'{ANILIST_TOKEN}',
        'Content-Type': 'application/json',
        'Accept': 'application/json',
    }
    try:
        response = requests.post('https://graphql.anilist.co', json={'query': query, 'variables': variables}, headers=headers, timeout=5)
        return response.status_code == 200
    except requests.exceptions.RequestException:
        return False

def get_anilist_id_from_mal(mal_id):
    """Traduce el MAL ID a AniList ID y obtiene el título Romaji."""
    query = '''
    query ($idMal: Int) {
        Media (idMal: $idMal, type: ANIME) {
            id
            title {
                romaji
            }
        }
    }
    '''
    variables = {'idMal': mal_id}
    try:
        response = requests.post('https://graphql.anilist.co', json={'query': query, 'variables': variables}, timeout=5)
        if response.status_code == 200:
            data = response.json().get('data', {})
            media = data.get('Media') if data else None
            if media:
                title = media.get('title', {}).get('romaji', 'Desconocido')
                return media.get('id', 0), title
    except Exception as e:
        print(f"⚠️ Error traduciendo MAL a AniList: {e}")
    return 0, "Desconocido"

def get_mal_id_from_shoko(shoko_series_id):
    """Consulta a Shoko por única vez para extraer el MAL ID de la serie."""
    headers = {"apikey": SHOKO_API_KEY, "Accept": "application/json"}
    url = f"http://{SHOKO_HOST}:{SHOKO_PORT}/api/v3/Series/{shoko_series_id}"
    try:
        res = requests.get(url, headers=headers, timeout=3)
        if res.status_code == 200:
            ids = res.json().get("IDs", {}) or {}
            mal_list = ids.get("MAL") or []
            return mal_list[0] if isinstance(mal_list, list) and len(mal_list) > 0 else 0
    except Exception as e:
        print(f"❌ [Error Shoko] No se pudo conectar con Shoko: {e}")
    return 0

def offline_living_worker():
    """El conserje revisa el Living Offline cada minuto con compresión y traducción diferida."""
    while True:
        pending = db_manager.get_pending_retries()
        if pending:
            print(f"\n[Conserje] 🛠️ Procesando {len(pending)} serie(s) pendiente(s) en el Living Offline...")
            for item in pending:
                anilist_id = item['anilist_id']
                shoko_series_id = item['shoko_series_id']

                # Si se guardó sin AniList ID (serie nueva vista estando offline)
                if anilist_id == 0:
                    mal_id = get_mal_id_from_shoko(shoko_series_id)
                    if mal_id:
                        anilist_id, romaji_name = get_anilist_id_from_mal(mal_id)
                        if anilist_id:
                            print(f"[Conserje] ✨ Serie traducida con éxito: {romaji_name} (AniList ID: {anilist_id})")
                            db_manager.save_new_series(shoko_series_id, 0, mal_id, anilist_id, romaji_name, item['episode'] - 1)
                        else:
                            print("[Conserje] ❌ AniList sigue inaccesible. Reintentando en 1 minuto.")
                            break
                    else:
                        print(f"[Conserje] ❌ No se obtuvo MAL ID para Shoko {shoko_series_id}.")
                        break

                # Enviar actualización a AniList
                success = post_to_anilist(anilist_id, item['episode'])
                if success:
                    print(f"[Conserje] ✅ Sincronizado: AniList ID {anilist_id} -> Ep {item['episode']}.")
                    db_manager.remove_from_queue(item['id'])
                    db_manager.update_episode_progress(shoko_series_id, item['episode'])
                    time.sleep(1.5)  # Espaciado de cortesía para la API
                else:
                    print(f"[Conserje] ❌ AniList sigue inaccesible. Reintentando en 1 minuto.")
                    break
        time.sleep(60)

class JellyfinWebhookHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def do_POST(self):
        content_length = int(self.headers.get('Content-Length', 0))
        post_data = self.rfile.read(content_length)

        try:
            data = json.loads(post_data.decode('utf-8'))
            if data.get("NotificationType") == "PlaybackStop" and data.get("ItemType") == "Episode":

                if not data.get("PlayedToCompletion", False):
                    self.send_response(200); self.end_headers(); return

                shoko_series_id = data.get("Provider_shoko series")
                episode_num = data.get("EpisodeNumber")
                anidb_id = data.get("Provider_anidb")

                if not shoko_series_id or not episode_num:
                    self.send_response(400); self.end_headers(); return

                shoko_series_id = int(shoko_series_id)
                episode_num = int(episode_num)

                print(f"\n📺 Sincronizando: Shoko Serie {shoko_series_id} -> Ep {episode_num}")

                # 1. Consultar Base de Datos local
                series_data = db_manager.get_series_data(shoko_series_id)

                if series_data:
                    anilist_id = series_data['anilist_id']
                    current_episode = series_data['current_episode']

                    if episode_num <= current_episode:
                        # Silencioso o aviso rápido de duplicado
                        self.send_response(200); self.end_headers(); return
                else:

                    # 2. Shoko habla por primera y única vez
                    mal_id = get_mal_id_from_shoko(shoko_series_id)
                    if not mal_id:
                        print(f"🌐❌ Shoko no tiene MAL ID aún para la serie {shoko_series_id}. Guardando en Living Offline...")
                        db_manager.add_to_queue(shoko_series_id, 0, episode_num)
                        self.send_response(200); self.end_headers(); return

                                                            # 3. Traducir MAL a AniList al vuelo y obtener Romaji
                    anilist_id, romaji_name = get_anilist_id_from_mal(mal_id)
                    if not anilist_id:
                        print(f"🌐❌ Sin conexión para traducir MAL ID {mal_id}. Guardando en Living Offline...")
                        db_manager.add_to_queue(shoko_series_id, 0, episode_num)
                        self.send_response(200); self.end_headers(); return

                    print(f"✨ Nueva serie detectada: {romaji_name}")
                    db_manager.save_new_series(shoko_series_id, anidb_id, mal_id, anilist_id, romaji_name, episode_num - 1)

                # 4. Hacer el POST a AniList
                success = post_to_anilist(anilist_id, episode_num)

                if success:
                    print(f"✅ Sincronizado con AniList (Anime ID: {anilist_id} - Ep: {episode_num})")
                    db_manager.update_episode_progress(shoko_series_id, episode_num)
                else:
                    print(f"🌐❌ AniList no respondió (Error de red/API). Enviando al 'Living Offline'.")
                    db_manager.add_to_queue(shoko_series_id, anilist_id, episode_num)

            self.send_response(200); self.end_headers()

        except Exception as e:
            print(f"❌ Error crítico procesando webhook: {e}")
            self.send_response(400); self.end_headers()

def run_server():
    threading.Thread(target=offline_living_worker, daemon=True).start()
    server_address = ('0.0.0.0', PORT)
    httpd = ThreadingHTTPServer(server_address, JellyfinWebhookHandler)
    print(f"🚀 Demonio Silencioso v0.7.6 corriendo en el puerto {PORT}...")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n🛑 Demonio detenido.")

if __name__ == '__main__':
    run_server()
