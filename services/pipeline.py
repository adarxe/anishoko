import re
import json
import logging
from datetime import datetime, timedelta
from rapidfuzz import fuzz
from database.repository import (
    get_mapping, 
    save_mapping, 
    add_to_queue,
    get_cached_relations,      # ← IMPORTA
    save_cached_relations      # ← IMPORTA
)
from clients.shoko import fetch_anilist_id_from_shoko
from clients.anilist import (
    fetch_media_info_from_anilist,
    fetch_franchise_relations_bfs,
    post_to_anilist,
    resolve_title_smart
)

logger = logging.getLogger("ShokoAniSync")

# TTL DA CACHE EM HORAS (7 dias = 168 horas)
RELATIONS_CACHE_TTL_HOURS = 168


def is_cache_valid(cached_at_str):
    """Verifica se cache ainda é válida baseado em TTL."""
    try:
        if not cached_at_str:
            return False
        # DuckDB retorna timestamp como string ISO
        cached_at = datetime.fromisoformat(cached_at_str.replace('Z', '+00:00'))
        expiry = cached_at + timedelta(hours=RELATIONS_CACHE_TTL_HOURS)
        return datetime.now(cached_at.tzinfo) < expiry if cached_at.tzinfo else datetime.now() < expiry
    except Exception as e:
        logger.warning("[Cache] Erro ao validar TTL: %s. Forçando redownload.", str(e))
        return False


def get_franchise_relations_with_cache(anilist_id):
    """
    Obtém relações de franquia com cache inteligente.
    
    Ordem:
    1. Tenta cache local (se válida)
    2. Se cache expirou/não existe, baixa fresh
    3. Salva para próxima vez
    
    Retorna: lista de animes relacionados
    """
    logger.info("[Cache Relations] Checando cache para base_id=%s", anilist_id)
    
    try:
        # PASSO 1: Tenta pegar do cache
        cached_data = get_cached_relations(anilist_id)
        
        if cached_data:
            logger.info("[Cache Relations] ✅ HIT! Usando dados cacheados (TTL: %dh)", RELATIONS_CACHE_TTL_HOURS)
            return cached_data
        
        # PASSO 2: Cache não existe, baixa fresh
        logger.info("[Cache Relations] ⚠️  MISS! Baixando relações fresh da API AniList...")
        discovered_anime = fetch_franchise_relations_bfs(anilist_id)
        
        # PASSO 3: Salva no cache para próxima vez
        if discovered_anime:
            save_cached_relations(anilist_id, discovered_anime)
            logger.info("[Cache Relations] 💾 Salvo em cache. Próximos hits serão instant!")
        
        return discovered_anime
        
    except Exception as e:
        logger.error("[Cache Relations] Erro durante cache logic: %s. Tentando fetch direto...", str(e))
        # Fallback: tenta buscar direto sem cache
        return fetch_franchise_relations_bfs(anilist_id)


def process_webhook_payload(shoko_series_id, episode, series_name):
    """
    Orquesta las 5 capas de resolución de AniList.
    Retorna True si se procesó de inmediato, o False si fue a la cola offline.
    """
    clean_series_name = re.sub(r'[^a-zA-Z0-9\s]', '', series_name).strip()
    search_query = f"{clean_series_name} {episode}"
    
    logger.info("[Pipeline] Procesando: '%s' (Ep: %s | Shoko ID: %s)", series_name, episode, shoko_series_id)

    # --------------------------------------------------------
    # CAPA 1: Caché L1 (Mapeo Directo)
    # --------------------------------------------------------
    anilist_id = get_mapping(shoko_series_id, episode)
    if anilist_id:
        logger.info("[Cache L1] Mapeo directo exacto encontrado -> AniList ID: %s", anilist_id)
        success = post_to_anilist(anilist_id, episode)
        if not success:
            add_to_queue(shoko_series_id, anilist_id, episode, search_query, series_name)
        return True

    # --------------------------------------------------------
    # CAPA 2: Bridge API Shoko (AniDB/MAL)
    # --------------------------------------------------------
    anilist_id = fetch_anilist_id_from_shoko(shoko_series_id)
    if anilist_id:
        logger.info("[Bridge Shoko] ID resuelto via Shoko API -> %s", anilist_id)
    
    # --------------------------------------------------------
    # CAPA 3: SmartResolver (Búsqueda GraphQL)
    # --------------------------------------------------------
    if not anilist_id:
        resolved_id, _ = resolve_title_smart(clean_series_name)
        if resolved_id:
            anilist_id = resolved_id
            logger.info("[SmartResolver] Búsqueda GraphQL completada -> AniList ID base %s", anilist_id)

    if not anilist_id:
        logger.warning("[Pipeline] Imposible resolver AniList ID. Encolando para reintento manual.")
        add_to_queue(shoko_series_id, 0, episode, search_query, series_name)
        return False

    # --------------------------------------------------------
    # CAPA 3.5: Desambiguación Relacional (Franchise BFS + Fuzzy)
    #           ⭐ COM CACHE AGORA! ⭐
    # --------------------------------------------------------
    api_format, _, _, _, _, _ = fetch_media_info_from_anilist(anilist_id)
    
    if api_format in ["MOVIE", "SPECIAL", "ONE_SHOT"]:
        logger.info("[Desambiguacion] Formato %s detectado. Iniciando Franchise BFS...", api_format)
        
        # ⭐ AQUI: Usa a função que verifica cache primeiro!
        discovered_anime = get_franchise_relations_with_cache(anilist_id)
        
        best_match_id = anilist_id
        highest_score = 0
        
        for anime in discovered_anime:
            t_romaji = anime["title"].get("romaji", "")
            t_english = anime["title"].get("english", "")
            
            score_romaji = fuzz.token_set_ratio(search_query, t_romaji)
            score_english = fuzz.token_set_ratio(search_query, t_english)
            max_score = max(score_romaji, score_english)
            
            if max_score > highest_score:
                highest_score = max_score
                best_match_id = anime["id"]
                
        if highest_score >= 85:
            logger.info("[FuzzyMatch] Coincidencia fuerte (%s%%) -> ID definitivo: %s", highest_score, best_match_id)
            anilist_id = best_match_id
        else:
            logger.warning("[FuzzyMatch] Confianza baja (%s%%). Usando ID base %s por precaución.", highest_score, anilist_id)

    # --------------------------------------------------------
    # CAPA 4: Ejecución y Guardado en Caché
    # --------------------------------------------------------
    save_mapping(shoko_series_id, episode, anilist_id, search_query, series_name)
    
    success = post_to_anilist(anilist_id, episode)
    if not success:
        logger.warning("[Pipeline] Falló mutación (Red/Rate Limit). Tarea enviada a la cola offline.")
        add_to_queue(shoko_series_id, anilist_id, episode, search_query, series_name)
        return False
        
    return True

