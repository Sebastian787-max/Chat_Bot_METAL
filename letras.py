"""Comprueba fragmentos contra letras de catálogo sin conservarlas."""

import logging
from difflib import SequenceMatcher
from time import monotonic
from urllib.parse import quote

import requests

from generos import normalizar


_LRCLIB = "https://lrclib.net/api"
_TIMEOUT = 8
_LOG = logging.getLogger(__name__)
_PALABRAS_COMUNES = frozenset(
    "a an the and or but if of to in on at for with as is are was were be been it its "
    "i me my you your he she we they them their that this there from so not no "
    "el la los las un una unos unas y o de del al en por para con sin a que se es "
    "son fue lo le les mi tu su sus te me nos yo el ella mas pero como".split()
)


def _pedir_json(url, timeout, *, params=None):
    if timeout <= 0:
        return None
    try:
        respuesta = requests.get(url, params=params, timeout=timeout)
        respuesta.raise_for_status()
        return respuesta.json()
    except (requests.RequestException, ValueError):
        return None


def _letra(campo, datos):
    valor = datos.get(campo) if isinstance(datos, dict) else None
    return valor if isinstance(valor, str) and valor.strip() else None


def _lrclib(artista, cancion):
    limite = monotonic() + _TIMEOUT
    parametros = {"artist_name": artista, "track_name": cancion}
    datos = _pedir_json(f"{_LRCLIB}/get", limite - monotonic(), params=parametros)
    letra = _letra("plainLyrics", datos)
    if letra:
        return letra
    resultados = _pedir_json(f"{_LRCLIB}/search", limite - monotonic(), params=parametros)
    if isinstance(resultados, list):
        esperado = (normalizar(artista), normalizar(cancion))
        for dato in resultados:
            if not isinstance(dato, dict):
                continue
            nombre = dato.get("artistName")
            titulo = dato.get("trackName")
            if not isinstance(nombre, str) or not isinstance(titulo, str):
                continue
            if (normalizar(nombre), normalizar(titulo)) == esperado:
                letra = _letra("plainLyrics", dato)
                if letra:
                    return letra
    return None


def _lyrics_ovh(artista, cancion):
    ruta = "/".join(quote(valor, safe="") for valor in (artista, cancion))
    return _letra("lyrics", _pedir_json(f"https://api.lyrics.ovh/v1/{ruta}", _TIMEOUT))


def _linea_similar(linea, lineas_letra):
    longitud = len(linea)
    for candidata in lineas_letra:
        if abs(len(candidata) - longitud) <= 2 and SequenceMatcher(
            None, linea, candidata, autojunk=False,
        ).ratio() >= 0.8:
            return True
    return False


def _coincide(fragmento, letra, cancion):
    excluir = set(normalizar(cancion).split())

    def lineas(texto):
        return [tuple(p for p in normalizar(linea).split() if p not in excluir)
                for linea in texto.splitlines()]

    lineas_fragmento = lineas(fragmento)
    lineas_letra = lineas(letra)
    distintas = {linea for linea in lineas_fragmento if len(linea) >= 4}
    if sum(_linea_similar(linea, lineas_letra) for linea in distintas) >= 2:
        return True
    significativas = {p for linea in lineas_fragmento for p in linea if len(p) > 2} - _PALABRAS_COMUNES
    palabras_letra = {p for linea in lineas_letra for p in linea}
    return bool(significativas and len(significativas & palabras_letra) * 5 >= len(significativas) * 3)


def verificar_letra(artista, cancion, fragmento):
    """Devuelve coincide, no_coincide o sin_datos para la canción consultada."""
    for servicio, consultar in (("LRCLIB", _lrclib), ("lyrics.ovh", _lyrics_ovh)):
        inicio = monotonic()
        letra = consultar(artista, cancion)
        resultado = "sin_datos"
        if letra:
            resultado = "coincide" if _coincide(fragmento, letra, cancion) else "no_coincide"
        _LOG.info("Letra %s: %s en %.2f s", servicio, resultado, monotonic() - inicio)
        if resultado != "sin_datos":
            return resultado
    return "sin_datos"
