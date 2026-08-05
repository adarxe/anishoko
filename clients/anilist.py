import time
import requests
import logging
from config import ANILIST_TOKEN
from database.connection import get_connection
from database.repository import get_cached_relations, save_cached_relations

logger = logging.getLogger("ShokoAniSync")

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
        res = requests.post(
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
        logger.error("[AniListClient] Error obteniendo metadatos (ID %s): %s", anilist_id, str(e))
    return None, None, None, None, None, None

def fetch_franchise_relations_bfs(base_anilist_id):
    """
    Realiza un BFS en AniList partiendo de base_anilist_id y recopila todos los nodos
    de la franquicia que sean de tipo ANIME. Usa la tabla relations_cache de DuckDB.
    """
    cached = get_cached_relations(base_anilist_id)
    if cached is not None:
        logger.info("[FranchiseBFS] Arbol de relaciones cargado desde caché local (Base ID %s)", base_anilist_id)
        return cached

    logger.info("[FranchiseBFS] Descargando arbol relacional completo desde AniList para ID %s...", base_anilist_id)
    
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

    while queue and len(visited) < 30:  # Límite preventivo de 30 nodos
        current_id = queue.pop(0)
        if current_id in visited:
            continue
        visited.add(current_id)

        try:
            res = requests.post(
                'https://graphql.anilist.co',
                json={'query': query, 'variables': {'id': int(current_id)}},
                timeout=10
            )
            time.sleep(0.25)  # Respetar rate limits de AniList

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
    logger.info("[FranchiseBFS] Arbol relacional finalizado -> %s obras ANIME descubiertas y cacheadas.", len(discovered_anime))
    return discovered_anime

def post_to_anilist(anilist_id, raw_target_episode, format_type="TV"):
    """Envía la mutación de actualización a AniList validando la idempotencia local."""
    target_episode = int(raw_target_episode)
    target_status = "CURRENT"

    with get_connection() as conn:
        row = conn.execute("SELECT episodes_watched, total_episodes FROM anilist_mirror WHERE anilist_id = ?", (anilist_id,)).fetchone()
        
        if row:
            current_watched, total_episodes = row
        else:
            api_format, api_episodes, api_status, t_romaji, t_english, api_mal_id = fetch_media_info_from_anilist(anilist_id)
            if not api_format:
                return False
            
            total_episodes = api_episodes or 999
            format_type = api_format
            current_watched = 0
            
            conn.execute('''
                INSERT INTO anilist_mirror (anilist_id, mal_id, title_romaji, title_english, format, status, user_status, episodes_watched, total_episodes)
                VALUES (?, ?, ?, ?, ?, ?, 'PLANNING', 0, ?)
            ''', (anilist_id, api_mal_id, t_romaji, t_english, format_type, api_status or "FINISHED", total_episodes))

    if format_type in ["MOVIE", "SPECIAL", "ONE_SHOT"] or total_episodes == 1:
        target_episode = 1
        target_status = "COMPLETED"
    
    if total_episodes and target_episode >= total_episodes:
        target_episode = total_episodes
        target_status = "COMPLETED"

    if target_episode <= current_watched:
        logger.info("[Validation] Mutacion cancelada por idempotencia (ID %s): Objetivo (%s) <= Actual (%s)", anilist_id, target_episode, current_watched)
        return True

    query = '''
    mutation ($mediaId: Int, $progress: Int, $status: MediaListStatus) {
      SaveMediaListEntry (mediaId: $mediaId, progress: $progress, status: $status) {
        id
        status
        progress
      }
    }
    '''
    
    headers = {
        'Authorization': f'Bearer {ANILIST_TOKEN}',
        'Content-Type': 'application/json',
        'Accept': 'application/json'
    }
    
    payload = {
        'query': query,
        'variables': {
            'mediaId': int(anilist_id),
            'progress': int(target_episode),
            'status': target_status
        }
    }

    try:
        res = requests.post(
            'https://graphql.anilist.co',
            json=payload,
            headers=headers,
            timeout=10
        )
        if res.status_code == 200:
            logger.info("[AniList API] Mutacion exitosa: ID %s -> Progreso: %s | Estado: %s", anilist_id, target_episode, target_status)
            try:
                with get_connection() as conn:
                    conn.execute("UPDATE anilist_mirror SET episodes_watched = ?, user_status = ? WHERE anilist_id = ?", (target_episode, target_status, anilist_id))
            except Exception as db_err:
                logger.error("[DuckDB] Error sincronizando espejo local: %s", str(db_err))
            return True
        else:
            logger.error("[AniList API] Rechazo del servidor: HTTP %s - %s", res.status_code, res.text)
            return False
            
    except requests.exceptions.RequestException as e:
        logger.error("[AniList API] Fallo de conexion externa: %s", str(e))
        return False

def resolve_title_smart(clean_title):
    """Busca el título en AniList mediante GraphQL y retorna su ID y formato."""
    query = '''
    query ($search: String) {
      Media (search: $search, type: ANIME) {
        id
        format
      }
    }
    '''
    try:
        res = requests.post(
            'https://graphql.anilist.co',
            json={'query': query, 'variables': {'search': clean_title}},
            timeout=10
        )
        if res.status_code == 200:
            media = res.json().get("data", {}).get("Media")
            if media:
                return media["id"], media["format"]
    except requests.exceptions.RequestException as e:
        logger.error("[SmartResolver] Fallo de red: %s", str(e))
    return None, "TV"

