import re
import logging
from rapidfuzz import fuzz

from database.repository import (
    get_mapping, 
    save_mapping, 
    get_cached_relations,      
    save_cached_relations,
    get_all_mirror_entries
)
from clients.shoko import fetch_mal_id_from_shoko
from clients.anilist import (
    fetch_franchise_relations_bfs,
    post_to_anilist,
    resolve_title_smart,
    get_anilist_id_by_mal
)

logger = logging.getLogger("ShokoAniSync")

STATUS_SUCCESS = "SUCCESS"
STATUS_UNRESOLVED = "UNRESOLVED"
STATUS_NETWORK_ERROR = "NETWORK_ERROR"
STATUS_REQUIRES_MANUAL = "REQUIRES_MANUAL"

def get_franchise_relations_with_cache(anilist_id):
    try:
        cached_data = get_cached_relations(anilist_id)
        if cached_data: return cached_data
        
        discovered_anime = fetch_franchise_relations_bfs(anilist_id)
        if discovered_anime:
            save_cached_relations(anilist_id, discovered_anime)
        return discovered_anime
    except Exception as e:
        logger.error("[Pipeline] Fallback de red en cache relacional: %s", str(e))
        return fetch_franchise_relations_bfs(anilist_id)

def resolve_with_bfs_relations(seed_id, search_title):
    franchise_tree = get_franchise_relations_with_cache(seed_id)
    if not franchise_tree: return None
        
    best_match_id = None
    highest_score = 0
    
    for node in franchise_tree:
        node_id = node.get("id")
        titles = node.get("title", {})
        score_romaji = fuzz.WRatio(search_title.lower(), (titles.get("romaji") or "").lower())
        score_english = fuzz.WRatio(search_title.lower(), (titles.get("english") or "").lower())
        
        max_node_score = max(score_romaji, score_english)
        if max_node_score > highest_score:
            highest_score = max_node_score
            best_match_id = node_id
            best_match_name = titles.get("romaji")
            
    if highest_score >= 85:
        logger.info("[SmartResolver] Mejor coincidencia en BFS: '%s' (ID %s | Score: %s)", best_match_name, best_match_id, highest_score)
        return best_match_id
    return None

def find_best_match_in_mirror(search_title):
    """Busca en el espejo local usando token_sort_ratio para priorizar temporadas."""
    entries = get_all_mirror_entries()
    if not entries: return None

    best_id = None
    highest_score = 0

    for anilist_id, t_romaji, t_english in entries:
        score_romaji = fuzz.token_sort_ratio(search_title.lower(), (t_romaji or "").lower())
        score_english = fuzz.token_sort_ratio(search_title.lower(), (t_english or "").lower())
        
        max_score = max(score_romaji, score_english)
        if max_score > highest_score:
            highest_score = max_score
            best_id = anilist_id

    if highest_score >= 80:
        logger.info("[MirrorMatch] Coincidencia en Espejo Local: '%s' (ID %s | Score: %s)", search_title, best_id, highest_score)
        return best_id
    return None

def process_webhook_payload(anidb_id, episode, series_name, item_name="", shoko_id=""):
    """Orquesta la resolución Offline-First con la arquitectura de 4 capas."""
    clean_series_name = re.sub(r'\b(19|20)\d{2}\b', '', series_name)
    clean_series_name = re.sub(r'[^a-zA-Z0-9\s]', '', clean_series_name).strip()
    
    clean_item_name = re.sub(r'\b(19|20)\d{2}\b', '', item_name)
    clean_item_name = re.sub(r'[^a-zA-Z0-9\s]', '', clean_item_name).strip()

    queue_query = f"{clean_series_name} {episode}".strip()
    full_search_title = f"{clean_series_name} {clean_item_name}".strip()

    logger.info("[Pipeline] Resolviendo: Título='%s' | Ep=%s | AniDB_ID=%s | Shoko_ID=%s", series_name, episode, anidb_id, shoko_id or "N/A")

    # PASO 1: Mapping L1 (0 ms) -> Requiere el episodio para la clave compuesta
    anilist_id = get_mapping(anidb_id, episode)
    if anilist_id:
        logger.info("[Pipeline] Paso 1 (Cache L1) superado -> ID %s", anilist_id)
        if post_to_anilist(anilist_id, episode, anidb_id=anidb_id):
            return STATUS_SUCCESS
        return STATUS_NETWORK_ERROR

    # PASO 2: Espejo Local L2.5 (0 ms) -> Match por token_sort_ratio
    anilist_id = find_best_match_in_mirror(full_search_title) or find_best_match_in_mirror(clean_series_name)
    if anilist_id:
        logger.info("[Pipeline] Paso 2 (Espejo Local) superado -> ID %s", anilist_id)
        save_mapping(anidb_id, episode, anilist_id, queue_query, series_name)
        if post_to_anilist(anilist_id, episode, anidb_id=anidb_id):
            return STATUS_SUCCESS
        return STATUS_NETWORK_ERROR

    # PASO 3: Shoko Bridge L2 (0 ms si está apagado) -> Solo consulta shoko_id
    if shoko_id:
        mal_id = fetch_mal_id_from_shoko(shoko_id)
        if mal_id:
            anilist_id = get_anilist_id_by_mal(mal_id)
            if anilist_id:
                logger.info("[Pipeline] Paso 3 (Shoko Bridge) superado -> ID %s", anilist_id)
                save_mapping(anidb_id, episode, anilist_id, queue_query, series_name)
                if post_to_anilist(anilist_id, episode, anidb_id=anidb_id):
                    return STATUS_SUCCESS
                return STATUS_NETWORK_ERROR

    # PASO 4: GraphQL API L3/L3.5 (Último recurso online)
    logger.info("[Pipeline] Paso 4 -> Iniciando búsqueda online en GraphQL...")
    seed_result = resolve_title_smart(clean_series_name)
    seed_id = seed_result[0] if isinstance(seed_result, tuple) else seed_result

    if seed_id == "NETWORK_ERROR":
        return STATUS_NETWORK_ERROR

    if not seed_id:
        return STATUS_UNRESOLVED

    anilist_id = resolve_with_bfs_relations(seed_id, full_search_title)
    if not anilist_id:
        logger.critical("[Pipeline] REQUIERE INTERVENCIÓN MANUAL para '%s'.", full_search_title)
        return STATUS_REQUIRES_MANUAL

    save_mapping(anidb_id, episode, anilist_id, queue_query, series_name)
    if post_to_anilist(anilist_id, episode, anidb_id=anidb_id):
        return STATUS_SUCCESS

    return STATUS_NETWORK_ERROR

