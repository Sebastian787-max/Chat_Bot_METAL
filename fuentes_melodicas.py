"""Descubrimiento y descarga acotada de partituras con procedencia explícita."""

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from time import monotonic, sleep
from urllib.parse import quote, urlsplit

import requests

from procesador_midi import MAX_ARCHIVO


OPENSCORE = "OpenScore/Lieder"
CC0 = "https://creativecommons.org/publicdomain/zero/1.0/"
LICENCIAS = {
    CC0: "CC0-1.0",
    "https://creativecommons.org/licenses/by/4.0/": "CC-BY-4.0",
}
EXTENSIONES = {".mid", ".midi", ".xml", ".musicxml", ".mxl"}
MAX_INDICE = 1024 * 1024
ULTIMO_ANIO_COMPOSITORES = 1925


class ErrorFuente(Exception):
    pass


class ArchivoRetirado(ErrorFuente):
    pass


@dataclass(frozen=True)
class Elemento:
    fuente: str
    identificador: str
    revision: str
    ubicacion: str
    url_original: str
    licencia_url: str
    evidencia: str
    atribucion: str
    categoria: str
    titulo: str = ""
    compositor: str = ""
    sha256: str = ""

    @property
    def clave(self):
        return f"{self.fuente}/{self.identificador}"

    @property
    def extension(self):
        return PurePosixPath(urlsplit(self.ubicacion).path).suffix.lower()


def url_https(url, hosts=None):
    partes = urlsplit(url)
    return (
        partes.scheme == "https" and bool(partes.hostname) and partes.netloc == partes.hostname
        and not partes.query and not partes.fragment and (hosts is None or partes.hostname in hosts)
    )


def leer_acotado(ruta, limite):
    with ruta.open("rb") as archivo:
        datos = archivo.read(limite + 1)
    if len(datos) > limite:
        raise ValueError("archivo superior al límite de tamaño")
    return datos


class Descargas:
    def __init__(self, sesion, pausa=1):
        self.sesion = sesion
        self.pausa = pausa
        self.proxima = 0.0

    def obtener(self, url, limite, hosts, *, parametros=None):
        if not url_https(url, hosts):
            raise ValueError("URL de descarga fuera de los orígenes habilitados")
        for intento in range(3):
            sleep(max(0, self.proxima - monotonic()))
            self.proxima = monotonic() + self.pausa
            espera = 2 ** intento
            try:
                with self.sesion.get(
                    url, params=parametros, timeout=(5, 12), allow_redirects=False, stream=True
                ) as r:
                    if r.status_code == 200:
                        datos = bytearray()
                        vencimiento = monotonic() + 30
                        for bloque in r.iter_content(16384):
                            if monotonic() > vencimiento:
                                raise requests.Timeout
                            datos.extend(bloque)
                            if len(datos) > limite:
                                raise ValueError("descarga superior al límite de tamaño")
                        return bytes(datos)
                    if r.status_code in (404, 410):
                        raise ArchivoRetirado(f"archivo no disponible (HTTP {r.status_code})")
                    if r.status_code not in (429, 500, 502, 503, 504):
                        raise ErrorFuente(f"HTTP {r.status_code}")
                    retry_after = r.headers.get("Retry-After")
                    if retry_after:
                        if not retry_after.isascii() or not retry_after.isdecimal() or len(retry_after) > 2:
                            raise ErrorFuente("servidor solicitó posponer la descarga")
                        espera = max(espera, int(retry_after))
                        if espera > 15:
                            raise ErrorFuente("servidor solicitó posponer la descarga")
            except requests.RequestException:
                pass
            self.proxima = max(self.proxima, monotonic() + espera)
        raise ErrorFuente("red o servicio temporalmente inaccesible tras tres intentos")


def openscore(descargas):
    github, raw = {"api.github.com"}, {"raw.githubusercontent.com"}
    api = f"https://api.github.com/repos/{OPENSCORE}"
    respuesta = json.loads(descargas.obtener(api + "/commits/main", MAX_INDICE, github))
    revision, arbol = respuesta["sha"], respuesta["commit"]["tree"]["sha"]
    if not all(re.fullmatch(r"[a-f0-9]{40}", sha) for sha in (revision, arbol)):
        raise ValueError("revisión de OpenScore inválida")
    base = f"https://raw.githubusercontent.com/{OPENSCORE}/{revision}/"
    licencia = descargas.obtener(base + "LICENSE.txt", 32_768, raw)
    if b"CC0 1.0 Universal" not in licencia or b"Creative Commons Legal Code" not in licencia:
        raise ValueError("licencia de la colección OpenScore no confirmada")
    biografias = descargas.obtener(base + "data/composers.yaml", MAX_INDICE, raw).decode("utf-8").replace("\r\n", "\n")
    compositores = set()
    # Solo interesan dos campos escalares del índice oficial. No se interpreta YAML.
    for ficha in re.split(r"(?m)^\d+:[ \t]*$", biografias):
        carpeta = re.search(r"(?m)^  path: ([^\r\n]+)$", ficha)
        fallecimiento = re.search(r"(?m)^  died: (\d{4})$", ficha)
        if carpeta and fallecimiento and int(fallecimiento[1]) <= ULTIMO_ANIO_COMPOSITORES:
            compositores.add(carpeta[1])
    if not compositores:
        raise ValueError("no se pudo comprobar la antigüedad de los compositores de OpenScore")
    indice = json.loads(descargas.obtener(
        api + f"/git/trees/{arbol}", 7 * MAX_INDICE, github, parametros={"recursive": "1"}
    ))
    if indice.get("truncated") is not False or not isinstance(indice.get("tree"), list):
        raise ValueError("índice de archivos de OpenScore incompleto")
    elementos = []
    for entrada in indice["tree"]:
        if (
            not isinstance(entrada, dict) or not isinstance(entrada.get("path"), str)
            or entrada.get("mode") != "100644"
        ):
            continue
        ruta = entrada["path"]
        partes = ruta.split("/")
        if len(partes) != 5 or partes[0] != "scores" or any(
            p in ("", ".", "..") or "\\" in p for p in partes[1:]
        ):
            continue
        if partes[1] not in compositores:
            continue
        nombre = re.fullmatch(r"lc([0-9]+)\.mxl", partes[-1])
        if not nombre:
            continue
        relativa = quote(ruta, safe="/")
        elementos.append(Elemento(
            "openscore", nombre[1], revision, base + relativa,
            f"https://github.com/{OPENSCORE}/blob/{revision}/{relativa}",
            CC0, f"https://github.com/{OPENSCORE}/blob/{revision}/LICENSE.txt",
            "OpenScore Lieder, edición digital del proyecto OpenScore", "clásica",
        ))
    if not elementos:
        raise ValueError("el índice OpenScore carece de partituras MusicXML reconocibles")
    return elementos


def manifiesto(ruta):
    """El publicador acredita composición y edición en un índice revisado por el operador."""
    registros = json.loads(leer_acotado(ruta, MAX_INDICE))
    if not isinstance(registros, list):
        raise ValueError("el manifiesto debe ser una lista JSON")
    elementos = []
    for item in registros:
        if not isinstance(item, dict):
            raise ValueError("entrada de manifiesto inválida")
        identificador = item.get("id", "")
        revision = item.get("sha256", "")
        if not isinstance(identificador, str) or not re.fullmatch(r"[\w.-]{1,80}", identificador):
            raise ValueError("identificador de manifiesto inválido")
        if not isinstance(revision, str) or not re.fullmatch(r"[a-f0-9]{64}", revision):
            raise ValueError("se requiere SHA-256 por archivo del manifiesto")
        campos = ("archivo", "url_original", "licencia_url", "evidencia", "atribucion", "titulo", "compositor")
        if any(not isinstance(item.get(c), str) or not 0 < len(item[c].strip()) <= 800 for c in campos):
            raise ValueError("faltan datos de identidad, archivo o procedencia en el manifiesto")
        categoria = item.get("categoria", "")
        if not isinstance(categoria, str) or len(categoria) > 80:
            raise ValueError("categoría de manifiesto inválida")
        # La afirmación documentada debe cubrir la composición y la edición concreta.
        licencia = item["licencia_url"] if item.get("composicion_y_edicion_autorizadas") is True else ""
        elementos.append(Elemento(
            "manifiesto", identificador, revision, item["archivo"], item["url_original"],
            licencia, item["evidencia"], item["atribucion"], categoria,
            item["titulo"], item["compositor"], revision,
        ))
    if len({e.clave for e in elementos}) != len(elementos):
        raise ValueError("identificadores duplicados en el manifiesto")
    return elementos


def descargar(elemento, descargas, ruta_manifiesto, hosts):
    if elemento.licencia_url not in LICENCIAS:
        raise ValueError("licencia incompatible o sin evidencia para composición y edición")
    if not all(url_https(url) for url in (elemento.url_original, elemento.evidencia)):
        raise ValueError("URL de procedencia o evidencia inválida")
    if elemento.extension not in EXTENSIONES:
        raise ValueError("tipo de archivo no admitido")
    if elemento.fuente == "openscore":
        datos = descargas.obtener(elemento.ubicacion, MAX_ARCHIVO, {"raw.githubusercontent.com"})
    elif elemento.ubicacion.startswith("https://"):
        datos = descargas.obtener(elemento.ubicacion, MAX_ARCHIVO, hosts)
    else:
        base = ruta_manifiesto.parent.resolve()
        archivo = (base / elemento.ubicacion).resolve()
        if not archivo.is_relative_to(base) or not archivo.is_file():
            raise ValueError("archivo local ausente o fuera de la carpeta del manifiesto")
        datos = leer_acotado(archivo, MAX_ARCHIVO)
    huella = hashlib.sha256(datos).hexdigest()
    if elemento.sha256 and huella != elemento.sha256:
        raise ValueError("el SHA-256 del archivo no coincide con el autorizado")
    return datos, huella
