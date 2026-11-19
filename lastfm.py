"""Cliente síncrono de Last.fm. Los handlers lo ejecutan en un hilo."""

import os
from html.parser import HTMLParser

import requests


URL_API = "https://ws.audioscrobbler.com/2.0/"
TIMEOUT_HTTP = (3, 5)


class ErrorLastFM(Exception):
    """Motivo de fallo seguro para mostrar al usuario."""


class _TextoBiografia(HTMLParser):
    def __init__(self):
        super().__init__()
        self.partes = []
        self.enlace = False

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self.enlace = True

    def handle_endtag(self, tag):
        if tag == "a":
            self.enlace = False

    def handle_data(self, data):
        if not self.enlace:
            self.partes.append(data)


def _consultar(metodo, **parametros):
    clave = os.getenv("LASTFM_API_KEY")
    if not clave:
        raise ErrorLastFM("configuracion")

    try:
        respuesta = requests.get(
            URL_API,
            params={"method": metodo, "api_key": clave, "format": "json", **parametros},
            timeout=TIMEOUT_HTTP,
        )
        if respuesta.status_code == 429:
            raise ErrorLastFM("limite")

        # Last.fm puede devolver un código propio junto con un estado HTTP de error.
        try:
            datos = respuesta.json()
        except ValueError:
            respuesta.raise_for_status()
            raise ErrorLastFM("servicio") from None

        if not isinstance(datos, dict):
            raise ErrorLastFM("servicio")
        codigo = datos.get("error")
        if codigo == 29:
            raise ErrorLastFM("limite")
        if codigo in (10, 26):
            raise ErrorLastFM("configuracion")
        if respuesta.status_code == 404 or codigo == 6:
            return {}
        respuesta.raise_for_status()
        if codigo:
            raise ErrorLastFM("servicio")
        return datos
    except requests.Timeout:
        raise ErrorLastFM("timeout") from None
    except requests.ConnectionError:
        raise ErrorLastFM("conexion") from None
    except requests.RequestException:
        raise ErrorLastFM("servicio") from None


def buscar_artista(nombre):
    """Devuelve el nombre canónico si hay una coincidencia exacta."""
    datos = _consultar("artist.search", artist=nombre, limit=10)
    artistas = datos.get("results", {}).get("artistmatches", {}).get("artist", [])
    normalizado = " ".join(nombre.split()).casefold()
    for artista in artistas:
        candidato = artista["name"]
        if " ".join(candidato.split()).casefold() == normalizado:
            return candidato
    return None


def obtener_similares(nombre, limite=5):
    """Devuelve hasta limite nombres, o None si el artista no aparece."""
    if limite <= 0:
        return []
    datos = _consultar("artist.getsimilar", artist=nombre, limit=limite, autocorrect=0)
    similares = datos.get("similarartists")
    if similares is None:
        return None
    return [artista["name"] for artista in similares.get("artist", [])[:limite]]


def obtener_top_artistas_por_genero(tag, limite=30):
    if limite <= 0:
        return []
    datos = _consultar("tag.gettopartists", tag=tag, limit=limite)
    return [artista["name"] for artista in datos.get("topartists", {}).get("artist", [])[:limite]]


def obtener_canciones_top(artista, limite=1):
    if limite <= 0:
        return []
    datos = _consultar("artist.gettoptracks", artist=artista, limit=limite, autocorrect=0)
    return [cancion["name"] for cancion in datos.get("toptracks", {}).get("track", [])[:limite]]


def buscar_pistas(cancion, artista=None, limite=5):
    parametros = {"track": cancion, "limit": limite}
    if artista:
        parametros["artist"] = artista
    datos = _consultar("track.search", **parametros)
    return datos.get("results", {}).get("trackmatches", {}).get("track", [])


def obtener_info_pista(cancion, artista):
    return _consultar("track.getInfo", track=cancion, artist=artista, autocorrect=0).get("track")


def obtener_info(nombre):
    """Devuelve el nombre, las etiquetas y una descripción breve en texto plano."""
    datos = _consultar("artist.getinfo", artist=nombre, lang="es", autocorrect=0)
    artista = datos.get("artist")
    if not artista:
        return None

    parser = _TextoBiografia()
    parser.feed(artista.get("bio", {}).get("summary") or "")
    parser.close()
    descripcion = " ".join("".join(parser.partes).split())
    if len(descripcion) > 450:
        descripcion = descripcion[:449].rsplit(" ", 1)[0] + "…"
    return {
        "nombre": artista["name"],
        "tags": [tag["name"] for tag in artista.get("tags", {}).get("tag", [])[:5]],
        "descripcion": descripcion,
    }
