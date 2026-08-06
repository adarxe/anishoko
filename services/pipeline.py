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


def process_webhook_payload(shoko_series_id, episode, series_name, item_name=""):
    """
    Orquesta las 5 capas de resolucion de AniList.
    item_name: Nombre especifico del capitulo/pelicula (Vital para OVAs y Movies).
    """
    clean_series_name = re.sub(r'[^a-zA-Z0-9\s]', '', series_name).strip()
    queue_query = f"{clean_series_name} {episode}"
    
    # Preparamos el nombre compuesto para desambiguacion fina (Ej: "Kizumonogatari Reiketsu Hen")
    clean_item_name = re.sub(r'[^a-zA-Z0-9\s]', '', item_name).strip()
    full_search_title = f"{clean_series_name} {clean_item_name}".strip()
    
    logger.info("[Pipeline] Iniciando resolucion: Título='%s' | Item='%s' | Ep=%s | ShokoID=%s", series_name, item_name, episode, shoko_series_id)

    # --------------------------------------------------------
    # CAPA 1: Cache L1 (Mapeo Directo DB)
    # --------------------------------------------------------
    anilist_id = get_mapping(shoko_series_id, episode)
    if anilist_id:
        logger.info("[Pipeline] Capa 1 superada -> Mapeo L1 encontrado: AniList ID %s", anilist_id)
        success = post_to_anilist(anilist_id, episode)
        if not success:
            add_to_queue(shoko_series_id, anilist_id, episode, queue_query, series_name)
        return True

    # --------------------------------------------------------
    # CAPA 1.5: Cache Local en Mirror (Búsqueda por Título Base)
    # --------------------------------------------------------
    # Buscamos primero por el titulo compuesto, si falla, por el nombre de la serie
    anilist_id = find_anilist_id_in_mirror_by_title(full_search_title) or find_anilist_id_in_mirror_by_title(clean_series_name)
    if anilist_id:
        logger.info("[Pipeline] Capa 1.5 superada -> Serie encontrada en anilist_mirror local: ID %s", anilist_id)
        save_mapping(shoko_series_id, episode, anilist_id, queue_query, series_name)
        success = post_to_anilist(anilist_id, episode)
        if not success:
            add_to_queue(shoko_series_id, anilist_id, episode, queue_query, series_name)
        return True

    # --------------------------------------------------------
    # CAPA 2: Bridge API Shoko
    # --------------------------------------------------------
    anilist_id = fetch_anilist_id_from_shoko(shoko_series_id)
    if anilist_id:
        logger.info("[Pipeline] Capa 2 superada -> ID resuelto via Shoko Bridge: %s", anilist_id)
    
    # --------------------------------------------------------
    # CAPA 3: SmartResolver (Búsqueda GraphQL)
    # --------------------------------------------------------
    if not anilist_id:
        # Intentamos resolver el titulo base primero
        resolved_id, _ = resolve_title_smart(clean_series_name)
        if resolved_id:
            anilist_id = resolved_id
            logger.info("[Pipeline] Capa 3 superada -> SmartResolver localizo ID base: %s", anilist_id)

    if not anilist_id:
        logger.warning("[Pipeline] Resolucion total fallida. Encolando tarea offline sin ID base.")
        add_to_queue(shoko_series_id, 0, episode, queue_query, series_name)
        return False

    # --------------------------------------------------------
    # CAPA 3.5: Desambiguacion Relacional (Franchise BFS + Fuzzy)
    # --------------------------------------------------------
    api_format, _, _, _, _, _ = fetch_media_info_from_anilist(anilist_id)
    
    if api_format and api_format in ["MOVIE", "SPECIAL", "ONE_SHOT"]:
        logger.info("[Pipeline] Desambiguacion activada: Formato '%s'. Contrastando titulo detallado: '%s'", api_format, full_search_title)
        
        discovered_anime = get_franchise_relations_with_cache(anilist_id)
        
        best_match_id = anilist_id
        highest_score = 0
        
        for anime in discovered_anime:
            t_romaji = anime["title"].get("romaji", "")
            t_english = anime["title"].get("english", "")
            
            # AHORA EVALUAMOS CONTRA EL TITULO COMPLETO (Ej: "Kizumonogatari Reiketsu Hen")
            score_romaji = fuzz.token_set_ratio(full_search_title, t_romaji)
            score_english = fuzz.token_set_ratio(full_search_title, t_english)
            max_score = max(score_romaji, score_english)
            
            if max_score > highest_score:
                highest_score = max_score
                best_match_id = anime["id"]
                
        if highest_score >= 80:  # Suavizado al 80% para peliculas largas
            logger.info("[Pipeline] Desambiguacion exitosa: Similitud %s%% -> ID definitivo ajustado a %s", round(highest_score, 2), best_match_id)
            anilist_id = best_match_id
        else:
            logger.warning("[Pipeline] Desambiguacion de baja confianza (%s%%). Se mantiene el ID base %s.", round(highest_score, 2), anilist_id)

    # --------------------------------------------------------
    # CAPA 4: Ejecucion Final y Actualizacion de L1
    # --------------------------------------------------------
    save_mapping(shoko_series_id, episode, anilist_id, queue_query, series_name)
    logger.info("[Pipeline] Mapeo guardado en Cache L1 para ID definitivo %s.", anilist_id)
    
    success = post_to_anilist(anilist_id, episode)
    if not success:
        logger.warning("[Pipeline] Mutacion AniList rechazada. Tarea enviada a cola offline.")
        add_to_queue(shoko_series_id, anilist_id, episode, queue_query, series_name)
        return False
        
    return True
