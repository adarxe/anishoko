import re
import logging
from rapidfuzz import fuzz
from database.repository import (
    get_mapping, 
    save_mapping, 
    add_to_queue,
    get_cached_relations,      
    save_cached_relations,
    find_anilist_id_in_mirror_by_title  # ← AGREGAR ESTA LÍNEA
)
from clients.shoko import fetch_anilist_id_from_shoko
from clients.anilist import (
    fetch_media_info_from_anilist,
    fetch_franchise_relations_bfs,
    post_to_anilist,
    resolve_title_smart
)

logger = logging.getLogger("ShokoAniSync")

def get_franchise_relations_with_cache(anilist_id):
    """
    Obtiene relaciones de franquicia validando cache local.
    Nota: El TTL de 7 dias debe ser gestionado por la query en repository.py.
    """
    logger.info("[Pipeline] Comprobando cache relacional para Base ID %s", anilist_id)
    
    try:
        cached_data = get_cached_relations(anilist_id)
        
        if cached_data:
            logger.info("[Pipeline] Acierto en cache relacional (Base ID %s)", anilist_id)
            return cached_data
        
        logger.info("[Pipeline] Fallo en cache relacional. Descargando arbol desde AniList API...")
        discovered_anime = fetch_franchise_relations_bfs(anilist_id)
        
        if discovered_anime:
            save_cached_relations(anilist_id, discovered_anime)
            logger.info("[Pipeline] Arbol relacional guardado en cache (Base ID %s)", anilist_id)
        
        return discovered_anime
        
    except Exception as e:
        logger.error("[Pipeline] Excepcion en gestion de cache relacional: %s. Aplicando fallback de red.", str(e))
        return fetch_franchise_relations_bfs(anilist_id)


# Constantes de estado de resolución
STATUS_SUCCESS = "SUCCESS"
STATUS_UNRESOLVED = "UNRESOLVED"
STATUS_NETWORK_ERROR = "NETWORK_ERROR"

def process_webhook_payload(shoko_series_id, episode, series_name, item_name=""):
    """
    Orquesta las capas de resolución de AniList.
    Devuelve: STATUS_SUCCESS, STATUS_UNRESOLVED o STATUS_NETWORK_ERROR.
    """
    clean_series_name = re.sub(r'[^a-zA-Z0-9\s]', '', series_name).strip()
    queue_query = f"{clean_series_name} {episode}"
    
    clean_item_name = re.sub(r'[^a-zA-Z0-9\s]', '', item_name).strip()
    full_search_title = f"{clean_series_name} {clean_item_name}".strip()
    
    logger.info("[Pipeline] Iniciando resolución: Título='%s' | Item='%s' | Ep=%s | ShokoID=%s", 
                series_name, item_name, episode, shoko_series_id)

    # CAPA 1: Cache L1 con TTL
    anilist_id = get_mapping(shoko_series_id, episode)
    if anilist_id:
        logger.info("[Pipeline] Capa 1 superada -> Mapeo L1 encontrado (TTL activo): AniList ID %s", anilist_id)
        # Refrescamos el timestamp para extender la vida del mapeo
        save_mapping(shoko_series_id, episode, anilist_id, queue_query, series_name)
        if post_to_anilist(anilist_id, episode, shoko_series_id=shoko_series_id):
            return STATUS_SUCCESS
        logger.warning("[Pipeline] Fallo de red en mutación (Capa 1).")
        return STATUS_NETWORK_ERROR

    # CAPA 1.5: Cache Local en Mirror
    anilist_id = find_anilist_id_in_mirror_by_title(full_search_title) or find_anilist_id_in_mirror_by_title(clean_series_name)
    if anilist_id:
        logger.info("[Pipeline] Capa 1.5 superada -> Serie encontrada en mirror: ID %s", anilist_id)
        save_mapping(shoko_series_id, episode, anilist_id, queue_query, series_name)
        if post_to_anilist(anilist_id, episode, shoko_series_id=shoko_series_id):
            return STATUS_SUCCESS
        return STATUS_NETWORK_ERROR

    # CAPA 2: Bridge API Shoko
    anilist_id = fetch_anilist_id_from_shoko(shoko_series_id)
    if anilist_id:
        logger.info("[Pipeline] Capa 2 superada -> ID resuelto vía Shoko Bridge: %s", anilist_id)
    
    # --------------------------------------------------------
    # CAPA 3: SmartResolver Escalonado
    # --------------------------------------------------------
    if not anilist_id:
        resolved_id, _ = resolve_title_smart(series_name)
        if resolved_id:
            anilist_id = resolved_id
            logger.info("[Pipeline] Capa 3 superada -> SmartResolver localizó ID semilla: %s", anilist_id)

    # Si tras la Capa 3 no logramos una semilla básica, se declara no resuelto
    if not anilist_id:
        logger.warning("[Pipeline] Imposible encontrar coincidencia o semilla para '%s'.", series_name)
        return STATUS_UNRESOLVED

    # --------------------------------------------------------
    # CAPA 3.5: Desambiguación Relacional Universal (BFS + RapidFuzz)
    # --------------------------------------------------------
    # Se ejecuta SIEMPRE para explorar la franquicia usando la semilla obtenida
    logger.info("[Pipeline] Explorando árbol relacional BFS (Semilla Base ID %s)...", anilist_id)
    discovered_anime = get_franchise_relations_with_cache(anilist_id)
    
    if discovered_anime:
        best_match_id = anilist_id
        highest_score = 0
        
        for anime in discovered_anime:
            t_romaji = anime["title"].get("romaji", "")
            t_english = anime["title"].get("english", "")
            
            # Evaluamos la similitud de la cadena completa contra cada nodo del árbol
            score_romaji = fuzz.token_set_ratio(full_search_title, t_romaji)
            score_english = fuzz.token_set_ratio(full_search_title, t_english)
            max_score = max(score_romaji, score_english)
            
            if max_score > highest_score:
                highest_score = max_score
                best_match_id = anime["id"]
                
        if highest_score >= 75:  # Umbral de confianza
            logger.info("[Pipeline] Desambiguación BFS exitosa: Coincidencia %s%% -> ID definitivo ajustado a %s", 
                        round(highest_score, 2), best_match_id)
            anilist_id = best_match_id
        else:
            logger.info("[Pipeline] Ningún nodo relacional superó el umbral (%s%%). Se conserva ID semilla %s.", 
                        round(highest_score, 2), anilist_id)

    # --------------------------------------------------------
    # CAPA 4: Ejecución Final y Guardado en Caché L1
    # --------------------------------------------------------
    save_mapping(shoko_series_id, episode, anilist_id, queue_query, series_name)
    if post_to_anilist(anilist_id, episode, shoko_series_id=shoko_series_id):
        return STATUS_SUCCESS
        
    logger.warning("[Pipeline] Fallo de red durante la mutación final en AniList.")
    return STATUS_NETWORK_ERROR

