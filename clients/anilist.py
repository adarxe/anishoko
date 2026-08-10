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

# Configuracion de Sesion y Resiliencia de Red
session = requests.Session()
retry_strategy = Retry(
    total=3,  # Maximo de 3 reintentos por fallo
    backoff_factor=1,  # Esperas de 1s, 2s, 4s entre reintentos
    status_forcelist=[429, 500, 502, 503, 504],  # Forzar reintento en estos codigos HTTP
    allowed_methods=["POST"]
)
adapter = HTTPAdapter(max_retries=retry_strategy)
session.mount("https://", adapter)
session.mount("http://", adapter)

# Inyeccion global de cabeceras
if ANILIST_TOKEN:
    session.headers.update({
        'Authorization': f'Bearer {ANILIST_TOKEN}',
        'Content-Type': 'application/json',
        'Accept': 'application/json'
    })
else:
    logger.warning("[AniListClient] ANILIST_TOKEN no configurado. Las mutaciones fallaran.")


def fetch_media_info_from_anilist(anilist_id):
    """Obtiene los metadatos completos de una obra en AniList."""
    query = '''
    query ($id: Int) {
      Media (id: $id, type: ANIME) {
        id
        idMal
        format
        episodes
        status
        title {
          romaji
          english
        }
      }
    }
    '''
    try:
        res = session.post(
            'https://graphql.anilist.co',
            json={'query': query, 'variables': {'id': int(anilist_id)}},
            timeout=10
        )
        if res.status_code == 200:
            data = res.json().get("data", {}).get("Media", {})
            if data:
                fmt = data.get("format", "TV")
                eps = data.get("episodes")
                status = data.get("status")
                t_romaji = data.get("title", {}).get("romaji", "")
                t_english = data.get("title", {}).get("english", "")
                mal_id = data.get("idMal")
                return fmt, eps, status, t_romaji, t_english, mal_id
    except requests.exceptions.RequestException as e:
        logger.error("[AniListClient] Error de red obteniendo metadatos (ID %s): %s", anilist_id, str(e))
    return None, None, None, None, None, None


def fetch_franchise_relations_bfs(base_anilist_id):
    """
    Realiza un BFS en AniList partiendo de base_anilist_id y recopila todos los nodos
    de la franquicia que sean de tipo ANIME. Usa cache local.
    """
    cached = get_cached_relations(base_anilist_id)
    if cached is not None:
        logger.info("[FranchiseBFS] Arbol relacional cargado desde cache (Base ID %s)", base_anilist_id)
        return cached

    logger.info("[FranchiseBFS] Descargando arbol relacional completo para ID %s...", base_anilist_id)
    
    query = '''
    query ($id: Int) {
      Media (id: $id) {
        id
        relations {
          edges {
            relationType
            node {
              id
              type
              format
              title {
                romaji
                english
              }
            }
          }
        }
      }
    }
    '''

    visited = set()
    queue = [base_anilist_id]
    discovered_anime = []

    while queue and len(visited) < 50:  # Limite preventivo de nodos
        current_id = queue.pop(0)
        if current_id in visited:
            continue
        visited.add(current_id)

        try:
            res = session.post(
                'https://graphql.anilist.co',
                json={'query': query, 'variables': {'id': int(current_id)}},
                timeout=10
            )
            time.sleep(0.3)  # Rate limit padding (90 rpm AniList limit)

            if res.status_code != 200:
                continue

            media = res.json().get("data", {}).get("Media")
            if not media:
                continue

            edges = media.get("relations", {}).get("edges", [])
            for edge in edges:
                node = edge.get("node")
                if not node:
                    continue
                node_id = node.get("id")
                node_type = node.get("type")

                if node_type == "ANIME":
                    if node_id not in [a["id"] for a in discovered_anime]:
                        discovered_anime.append({
                            "id": node_id,
                            "format": node.get("format", "TV"),
                            "title": node.get("title", {})
                        })
                    if node_id not in visited and node_id not in queue:
                        queue.append(node_id)

        except requests.exceptions.RequestException as e:
            logger.error("[FranchiseBFS] Error de red explorando nodo %s: %s", current_id, str(e))

    save_cached_relations(base_anilist_id, discovered_anime)
    logger.info("[FranchiseBFS] Arbol relacional completado -> %s nodos ANIME cacheados.", len(discovered_anime))
    return discovered_anime


def post_to_anilist(anilist_id, raw_target_episode, format_type="TV", shoko_series_id=""):
    """Envia mutaciones a AniList respetando el contador de rewatch de AniList Web."""
    target_episode = int(raw_target_episode)
    
    with get_connection() as conn:
        row = conn.execute(
            "SELECT episodes_watched, total_episodes, user_status, repeat_count FROM anilist_mirror WHERE anilist_id = ?",
            (anilist_id,)
        ).fetchone()

        if row:
            current_watched, total_episodes, user_status, repeat_count = row
            repeat_count = repeat_count or 0
        else:
            api_format, api_episodes, api_status, t_romaji, t_english, api_mal_id = fetch_media_info_from_anilist(anilist_id)
            if not api_format:
                return False

            total_episodes = api_episodes or 999
            format_type = api_format
            current_watched = 0
            user_status = "PLANNING"
            repeat_count = 0

            conn.execute('''
                INSERT INTO anilist_mirror (anilist_id, mal_id, title_romaji, title_english, format, status, user_status, episodes_watched, total_episodes, repeat_count)
                VALUES (?, ?, ?, ?, ?, ?, 'PLANNING', 0, ?, 0)
            ''', (anilist_id, api_mal_id, t_romaji, t_english, format_type, api_status or "FINISHED", total_episodes))

    is_completed_previously = (user_status == "COMPLETED")
    is_currently_rewatching = (user_status == "REPEATING")
    is_single_entry = (format_type in ["MOVIE", "SPECIAL", "ONE_SHOT"] or total_episodes == 1)

    target_status = user_status
    target_repeat = repeat_count

    # --- MÁQUINA DE ESTADOS REWATCH ---
    
    # CASO A: Película u OVA previamente COMPLETADA (Suma +1 de inmediato)
    if is_completed_previously and is_single_entry:
        target_episode = 1
        target_status = "COMPLETED"
        target_repeat = repeat_count + 1
        logger.info("[Rewatch] Película/OVA completada nuevamente. Incrementando rewatch a %s", target_repeat)

    # CASO B: Inicio de Rewatch en Serie (Cualquier episodio visto en obra COMPLETADA)
    elif is_completed_previously:
        logger.info("[Rewatch] Inicio de re-visualizacion detectado. Estado: COMPLETED -> REPEATING")
        target_status = "REPEATING"
        current_watched = 0  # Desactivamos el bloqueo de idempotencia para la nueva ronda

    # CASO C: En curso de Rewatch
    elif is_currently_rewatching:
        target_status = "REPEATING"

    # CASO D: Finalización de serie en Rewatch o primera vez
    if not is_single_entry and total_episodes and target_episode >= total_episodes:
        target_episode = total_episodes
        target_status = "COMPLETED"
        if is_currently_rewatching:
            target_repeat = repeat_count + 1
            logger.info("[Rewatch] Serie completada en Rewatch! Contador incrementado a %s", target_repeat)

    # --- VALIDACIÓN DE IDEMPOTENCIA ---
    # Se aplica solo en primera vez o durante rewatch si llega un ep menor al ya visto en esta ronda
    if not (is_completed_previously and is_single_entry) and not (is_completed_previously and not is_currently_rewatching) and target_episode <= current_watched and user_status == target_status:
        logger.info("[Validation] Mutacion cancelada (Idempotencia): ID %s | Objetivo (%s) <= Actual (%s)", anilist_id, target_episode, current_watched)
        return True

    # --- MUTACIÓN GRAPHQL CON PARÁMETRO REPEAT PROTEGIDO ---
    query = '''
    mutation ($mediaId: Int, $progress: Int, $status: MediaListStatus, $repeat: Int) {
      SaveMediaListEntry (mediaId: $mediaId, progress: $progress, status: $status, repeat: $repeat) {
        id
        status
        progress
        repeat
      }
    }
    '''

    payload = {
        'query': query,
        'variables': {
            'mediaId': int(anilist_id),
            'progress': int(target_episode),
            'status': target_status,
            'repeat': int(target_repeat)
        }
    }

    try:
        res = session.post('https://graphql.anilist.co', json=payload, timeout=10)
        if res.status_code == 200:
            logger.info("[AniListClient] Mutacion exitosa: ID %s -> Progreso: %s | Estado: %s | Repeticiones: %s", 
                        anilist_id, target_episode, target_status, target_repeat)
            try:
                update_mirror_local_watch(anilist_id, target_episode, target_status, target_repeat)
                add_to_watch_history(anilist_id, target_episode, shoko_series_id)
            except Exception as db_err:
                logger.error("[Database] Error actualizando estado de espejo o historial: %s", str(db_err))
            return True
        else:
            logger.error("[AniListClient] Rechazo de mutacion: HTTP %s - %s", res.status_code, res.text)
            return False

    except requests.exceptions.RequestException as e:
        logger.error("[AniListClient] Fallo de conexion en mutacion: %s", str(e))
        return False

def resolve_title_smart(clean_title):
    """
    Busca el título en AniList mediante GraphQL utilizando una estrategia 
    de búsqueda escalonada (fallback de términos) para obtener una semilla.
    """
    candidates = [clean_title]
    
    # 1. Recorte por delimitadores comunes (ej: "Nekomonogatari (Kuro): Tsubasa..." -> "Nekomonogatari")
    for delim in [':', '-', '(']:
        if delim in clean_title:
            base = clean_title.split(delim)[0].strip()
            if base and base not in candidates:
                candidates.append(base)
                
    # 2. Recorte de sub-títulos o cadenas largas
    words = clean_title.split()
    if len(words) > 2:
        two_words = " ".join(words[:2])
        if two_words not in candidates:
            candidates.append(two_words)
        if words[0] not in candidates:
            candidates.append(words[0])

    query = '''
    query ($search: String) {
      Media (search: $search, type: ANIME) {
        id
        format
      }
    }
    '''
    
    for candidate in candidates:
        if not candidate or len(candidate) < 2:
            continue
        try:
            res = session.post(
                'https://graphql.anilist.co',
                json={'query': query, 'variables': {'search': candidate}},
                timeout=10
            )
            time.sleep(0.3)  # Rate limit padding
            if res.status_code == 200:
                media = res.json().get("data", {}).get("Media")
                if media:
                    logger.info("[SmartResolver] Semilla localizada con término: '%s' -> ID AniList: %s", candidate, media["id"])
                    return media["id"], media["format"]
        except requests.exceptions.RequestException as e:
            logger.error("[SmartResolver] Error de red resolviendo término ('%s'): %s", candidate, str(e))
            
    return None, "TV"
