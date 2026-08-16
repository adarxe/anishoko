import time
import requests
import logging
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from config import ANILIST_TOKEN
from database.connection import get_connection
from database.repository import (
    get_cached_relations, 
    save_cached_relations,
    update_mirror_local_watch,
    add_to_watch_history
)

logger = logging.getLogger("ShokoAniSync")

session = requests.Session()
retry_strategy = Retry(
    total=3,
    backoff_factor=1,
    status_forcelist=[429, 500, 502, 503, 504],
    allowed_methods=["POST"]
)
adapter = HTTPAdapter(max_retries=retry_strategy)
session.mount("https://", adapter)
session.mount("http://", adapter)

if ANILIST_TOKEN:
    session.headers.update({
        'Authorization': f'Bearer {ANILIST_TOKEN}',
        'Content-Type': 'application/json',
        'Accept': 'application/json'
    })
else:
    logger.warning("[AniListClient] ANILIST_TOKEN no configurado. Las mutaciones fallaran.")

def fetch_media_info_from_anilist(anilist_id):
    query = '''query ($id: Int) { Media (id: $id, type: ANIME) { id idMal format episodes status title { romaji english } } }'''
    try:
        res = session.post('https://graphql.anilist.co', json={'query': query, 'variables': {'id': int(anilist_id)}}, timeout=10)
        if res.status_code == 200:
            data = res.json().get("data", {}).get("Media", {})
            if data:
                return (data.get("format", "TV"), data.get("episodes"), data.get("status"), 
                        data.get("title", {}).get("romaji", ""), data.get("title", {}).get("english", ""), data.get("idMal"))
    except requests.exceptions.RequestException as e:
        logger.error("[AniListClient] Error de red obteniendo metadatos (ID %s): %s", anilist_id, str(e))
    return None, None, None, None, None, None

def fetch_franchise_relations_bfs(base_anilist_id):
    cached = get_cached_relations(base_anilist_id)
    if cached is not None:
        return cached

    logger.info("[FranchiseBFS] Descargando árbol relacional compacto (1-Shot GraphQL) para ID %s...", base_anilist_id)
    query = '''query ($id: Int) { Media (id: $id) { id relations { edges { node { id type format title { romaji english } relations { edges { node { id type format title { romaji english } } } } } } } } }'''
    discovered_anime = []
    seen_ids = set()

    try:
        res = session.post('https://graphql.anilist.co', json={'query': query, 'variables': {'id': int(base_anilist_id)}}, timeout=5)
        if res.status_code == 200:
            media = res.json().get("data", {}).get("Media")
            if media:
                seen_ids.add(base_anilist_id)
                for edge1 in media.get("relations", {}).get("edges", []):
                    node1 = edge1.get("node")
                    if node1 and node1.get("type") == "ANIME" and node1.get("id") not in seen_ids:
                        n1_id = node1.get("id")
                        seen_ids.add(n1_id)
                        discovered_anime.append({"id": n1_id, "format": node1.get("format", "TV"), "title": node1.get("title", {})})
                        for edge2 in node1.get("relations", {}).get("edges", []):
                            node2 = edge2.get("node")
                            if node2 and node2.get("type") == "ANIME" and node2.get("id") not in seen_ids:
                                n2_id = node2.get("id")
                                seen_ids.add(n2_id)
                                discovered_anime.append({"id": n2_id, "format": node2.get("format", "TV"), "title": node2.get("title", {})})
    except requests.exceptions.RequestException as e:
        logger.error("[FranchiseBFS] Error de red: %s", str(e))

    save_cached_relations(base_anilist_id, discovered_anime)
    return discovered_anime

def post_to_anilist(anilist_id, raw_target_episode, format_type="TV", anidb_id=""):
    target_episode = int(raw_target_episode)
    
    with get_connection() as conn:
        row = conn.execute("SELECT episodes_watched, total_episodes, user_status, repeat_count FROM anilist_mirror WHERE anilist_id = ?", (anilist_id,)).fetchone()
        if row:
            current_watched, total_episodes, user_status, repeat_count = row
            repeat_count = repeat_count or 0
        else:
            api_format, api_episodes, api_status, t_romaji, t_english, api_mal_id = fetch_media_info_from_anilist(anilist_id)
            if not api_format: return False
            total_episodes = api_episodes or 999
            format_type = api_format
            current_watched = 0
            user_status = "PLANNING"
            repeat_count = 0
            conn.execute('''INSERT INTO anilist_mirror (anilist_id, mal_id, title_romaji, title_english, format, status, user_status, episodes_watched, total_episodes, repeat_count) VALUES (?, ?, ?, ?, ?, ?, 'PLANNING', 0, ?, 0)''', (anilist_id, api_mal_id, t_romaji, t_english, format_type, api_status or "FINISHED", total_episodes))

    is_completed_previously = (user_status == "COMPLETED")
    is_currently_rewatching = (user_status == "REPEATING")
    is_single_entry = (format_type in ["MOVIE", "SPECIAL", "ONE_SHOT"] or total_episodes == 1)

    target_status = user_status
    target_repeat = repeat_count

    # --- MÁQUINA DE ESTADOS REWATCH ---
    if is_completed_previously and is_single_entry:
        target_episode = 1
        target_status = "COMPLETED"
        target_repeat = repeat_count + 1
    elif is_completed_previously:
        # REWATCH GUARD: Solo permite rewatch si es el episodio 1 o 2.
        if target_episode <= 2:
            logger.info("[Rewatch] Inicio de re-visualizacion detectado. Estado: COMPLETED -> REPEATING")
            target_status = "REPEATING"
            current_watched = 0 
        else:
            logger.warning("[Rewatch Guard] Posible falso Rewatch evitado. Intentando ep %s en serie ya COMPLETADA.", target_episode)
            return True # Tratado como éxito para eliminar de cola sin mutar
    elif is_currently_rewatching:
        target_status = "REPEATING"

    if not is_single_entry and total_episodes and target_episode >= total_episodes:
        target_episode = total_episodes
        target_status = "COMPLETED"
        if is_currently_rewatching:
            target_repeat = repeat_count + 1

    # --- VALIDACIÓN DE IDEMPOTENCIA ---
    if not (is_completed_previously and is_single_entry) and not (is_completed_previously and not is_currently_rewatching) and target_episode <= current_watched and user_status == target_status:
        logger.info("[Validation] Mutacion cancelada (Idempotencia): ID %s | Objetivo (%s) <= Actual (%s)", anilist_id, target_episode, current_watched)
        return True

    query = '''mutation ($mediaId: Int, $progress: Int, $status: MediaListStatus, $repeat: Int) { SaveMediaListEntry (mediaId: $mediaId, progress: $progress, status: $status, repeat: $repeat) { id status progress repeat } }'''
    payload = {'query': query, 'variables': {'mediaId': int(anilist_id), 'progress': int(target_episode), 'status': target_status, 'repeat': int(target_repeat)}}

    try:
        res = session.post('https://graphql.anilist.co', json=payload, timeout=10)
        if res.status_code == 200:
            logger.info("[AniListClient] Mutacion exitosa: ID %s -> Progreso: %s | Estado: %s | Repeticiones: %s", anilist_id, target_episode, target_status, target_repeat)
            update_mirror_local_watch(anilist_id, target_episode, target_status, target_repeat)
            add_to_watch_history(anidb_id, target_episode, anilist_id)
            return True
        logger.error("[AniListClient] Rechazo de mutacion: HTTP %s - %s", res.status_code, res.text)
        return False
    except requests.exceptions.RequestException as e:
        logger.error("[AniListClient] Fallo de conexion en mutacion: %s", str(e))
        return False

def resolve_title_smart(clean_title):
    candidates = [clean_title]
    for delim in [':', '-', '(']:
        if delim in clean_title:
            base = clean_title.split(delim)[0].strip()
            if base and base not in candidates: candidates.append(base)
                
    words = clean_title.split()
    if len(words) > 2:
        two_words = " ".join(words[:2])
        if two_words not in candidates: candidates.append(two_words)
        if words[0] not in candidates: candidates.append(words[0])

    query = '''query ($search: String) { Media (search: $search, type: ANIME) { id format } }'''
    network_failed = False
    
    for candidate in candidates:
        if not candidate or len(candidate) < 2: continue
        try:
            res = session.post('https://graphql.anilist.co', json={'query': query, 'variables': {'search': candidate}}, timeout=10)
            time.sleep(0.3)
            if res.status_code == 200:
                media = res.json().get("data", {}).get("Media")
                if media: return media["id"], media["format"]
        except requests.exceptions.RequestException as e:
            logger.error("[SmartResolver] Error de red ('%s'): %s", candidate, str(e))
            network_failed = True
            
    if network_failed: return "NETWORK_ERROR", None
    return None, "TV"

def get_anilist_id_by_mal(mal_id):
    if not mal_id: return None
    query = '''query ($idMal: Int) { Media (idMal: $idMal, type: ANIME) { id } }'''
    try:
        res = session.post('https://graphql.anilist.co', json={'query': query, 'variables': {'idMal': int(mal_id)}}, timeout=10)
        if res.status_code == 200:
            anilist_id = res.json().get("data", {}).get("Media", {}).get("id")
            if anilist_id:
                logger.info("[AniListClient] Traducción exitosa: MAL ID %s -> AniList ID %s", mal_id, anilist_id)
                return anilist_id
    except Exception as e:
        logger.warning("[AniListClient] Error traduciendo MAL ID %s: %s", mal_id, str(e))
    return None

