"""Busca canciones y carátulas en iTunes y Last.fm."""

from difflib import SequenceMatcher
from urllib.parse import urlsplit

import requests

from generos import normalizar
from lastfm import TIMEOUT_HTTP, ErrorLastFM, buscar_pistas, obtener_info_pista


def _mejor_coincidencia(opciones, cancion, artista, *, titulo_exacto=False):
    titulo_buscado = normalizar(cancion)
    artista_buscado = normalizar(artista or "")
    minimo = 1 if titulo_exacto else 0.82
    clasificados = []
    for opcion in opciones:
        titulo = normalizar(opcion.get("cancion") or "")
        nombre = normalizar(opcion.get("artista") or "")
        if not titulo or not nombre:
            continue
        similitud_titulo = SequenceMatcher(None, titulo_buscado, titulo).ratio()
        similitud_artista = SequenceMatcher(None, artista_buscado, nombre).ratio() if artista_buscado else 1
        if similitud_titulo >= minimo and similitud_artista >= minimo:
            clasificados.append((similitud_titulo + similitud_artista, opcion))
    return max(clasificados, key=lambda par: par[0])[1] if clasificados else None


def _url_portada(url):
    if not url:
        return None
    partes = urlsplit(url)
    if partes.scheme not in ("http", "https") or not partes.hostname:
        return None
    ruta = partes.path.lower()
    if any(indicio in ruta for indicio in ("2a96cbd8b46e442fc41c2b86b821562f", "placeholder", "noimage")):
        return None
    return url


def _buscar_itunes(cancion, artista, *, titulo_exacto=False):
    try:
        respuesta = requests.get(
            "https://itunes.apple.com/search",
            params={"term": " ".join(filter(None, (cancion, artista))),
                    "entity": "song", "limit": 5, "country": "US"},
            timeout=TIMEOUT_HTTP,
        )
        if respuesta.status_code == 429:
            raise ErrorLastFM("limite")
        respuesta.raise_for_status()
        datos = respuesta.json()
    except requests.Timeout:
        raise ErrorLastFM("timeout") from None
    except requests.ConnectionError:
        raise ErrorLastFM("conexion") from None
    except (requests.RequestException, ValueError):
        raise ErrorLastFM("servicio") from None
    if not isinstance(datos, dict) or not isinstance(datos.get("results"), list):
        raise ErrorLastFM("servicio")

    return _mejor_coincidencia((
        {
            "cancion": pista.get("trackName"),
            "artista": pista.get("artistName"),
            "album": pista.get("collectionName"),
            "portada": _url_portada((pista.get("artworkUrl100") or "").replace("100x100", "600x600")),
        }
        for pista in datos["results"] if isinstance(pista, dict)
    ), cancion, artista, titulo_exacto=titulo_exacto)


def _buscar_lastfm(cancion, artista, *, titulo_exacto=False):
    pistas = buscar_pistas(cancion, artista)
    coincidencia = _mejor_coincidencia((
        {"cancion": pista.get("name"), "artista": pista.get("artist"), "album": None,
         "portada": _portada_lastfm(pista)}
        for pista in pistas
    ), cancion, artista, titulo_exacto=titulo_exacto)
    if not coincidencia:
        return None
    try:
        ficha = obtener_info_pista(coincidencia["cancion"], coincidencia["artista"]) or {}
    except ErrorLastFM:
        return coincidencia
    album = ficha.get("album") or {}
    coincidencia["album"] = album.get("title")
    coincidencia["portada"] = _portada_lastfm(album) or coincidencia["portada"]
    return coincidencia


def _portada_lastfm(datos):
    imagenes = datos.get("image") or []
    for imagen in reversed(imagenes):
        url = _url_portada(imagen.get("#text"))
        if url:
            return url
    return None


def buscar_cancion(cancion, artista=None, *, titulo_exacto=False):
    """Devuelve una coincidencia de catálogo, incluso si no tiene carátula."""
    error_itunes = None
    try:
        pista = _buscar_itunes(cancion, artista, titulo_exacto=titulo_exacto)
    except ErrorLastFM as error:
        error_itunes = error
    else:
        if pista:
            return pista
    try:
        return _buscar_lastfm(cancion, artista, titulo_exacto=titulo_exacto)
    except ErrorLastFM:
        if error_itunes:
            raise error_itunes from None
        raise
