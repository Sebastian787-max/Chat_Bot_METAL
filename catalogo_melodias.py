"""Importa grabaciones monofónicas autorizadas al catálogo local."""

import argparse
import json
import os
import sys
from contextlib import contextmanager
from pathlib import Path
from tempfile import NamedTemporaryFile
from time import sleep

import numpy as np
from dotenv import load_dotenv

from generos import normalizar
from reconocimiento_audio import (
    TASA_AUDIO, _identidad, decodificar_audio, extraer_melodia, leer_datos_catalogo,
    preparar_audio, validar_catalogo,
)


VARIABLE = "MOSHPIT_CATALOGO_MELODIAS"
FORMATOS = {".wav", ".mp3", ".flac", ".ogg"}
MAX_BYTES = 50 * 1024 * 1024
MAX_CATALOGO = 2_000_000


def leer_catalogo(ruta):
    datos = leer_datos_catalogo(ruta)
    validar_catalogo(datos)
    return datos


def extraer_fragmento(ruta):
    try:
        extension = ruta.suffix.lower()
        if extension not in FORMATOS or not ruta.is_file():
            raise ValueError("se requiere un archivo WAV, MP3, FLAC u OGG")
        with ruta.open("rb") as archivo:
            datos = archivo.read(MAX_BYTES + 1)
        if not 0 < len(datos) <= MAX_BYTES:
            raise ValueError("el archivo está vacío o supera 50 MiB")
        cabecera = {
            ".wav": datos[:4] in (b"RIFF", b"RF64") and datos[8:12] == b"WAVE",
            ".mp3": datos.startswith(b"ID3") or (
                len(datos) >= 2 and datos[0] == 255 and datos[1] & 0xE0 == 0xE0
            ),
            ".flac": datos.startswith((b"fLaC", b"ID3")),
            ".ogg": datos.startswith(b"OggS"),
        }
        if not cabecera[extension]:
            raise ValueError("la cabecera no coincide con la extensión")
        pcm = decodificar_audio(datos, formato=extension[1:])
        if pcm is None:
            raise ValueError("no se pudo decodificar el audio. Comprueba el archivo y FFmpeg en el PATH")
        if len(pcm) > 30 * TASA_AUDIO:
            raise ValueError("recorta la grabación a un fragmento de hasta 30 segundos")
        segmento, _ = preparar_audio(datos, pcm)
        notas = extraer_melodia(segmento, cobertura_minima=0.65) if segmento is not None else []
        return normalizar_fragmento(notas)
    except (OSError, ValueError) as error:
        raise ValueError(f"{ruta.name}: {error}") from error


def normalizar_fragmento(notas):
    if not 8 <= len(notas) <= 128:
        raise ValueError("se requieren entre 8 y 128 notas estables con altura definida")
    alturas, duraciones = np.asarray(notas).T
    intervalos = np.diff(alturas)
    ancho = min(len(intervalos), 16)
    ventanas = np.lib.stride_tricks.sliding_window_view(intervalos, ancho)
    if not any(len(set(frase)) >= 3 and np.count_nonzero(frase) >= 5 for frase in ventanas):
        raise ValueError("la frase tiene muy poca variedad melódica para el reconocedor")
    duraciones_relativas = duraciones / np.median(duraciones)
    if np.any((duraciones_relativas < 0.01) | (duraciones_relativas > 100)):
        raise ValueError("proporciones rítmicas fuera del rango del catálogo")
    return [[int(nota), round(float(duracion), 6)] for nota, duracion in zip(alturas, duraciones_relativas)]


def clave_cancion(pista):
    return normalizar(pista["cancion"]), normalizar(pista["artista"])


def mismo_fragmento(primero, segundo):
    if len(primero) != len(segundo):
        return False
    a, b = np.asarray(primero), np.asarray(segundo)
    if not np.array_equal(np.diff(a[:, 0]), np.diff(b[:, 0])):
        return False
    ritmo = np.log(a[:, 1]) - np.log(b[:, 1])
    return bool(np.all(abs(ritmo - np.median(ritmo)) <= np.log(1.25)))


def escribir_json(ruta, datos, limite, *, indentacion=None):
    separadores = (",", ":") if indentacion is None else None
    contenido = (json.dumps(datos, ensure_ascii=False, indent=indentacion, separators=separadores) + "\n").encode(
        "utf-8"
    )
    if len(contenido) > limite:
        raise ValueError("se alcanzó el límite de almacenamiento")
    temporal = None
    try:
        with NamedTemporaryFile(dir=ruta.parent, prefix=ruta.name + ".", suffix=".tmp",
                                delete=False, mode="wb") as archivo:
            temporal = Path(archivo.name)
            archivo.write(contenido)
            archivo.flush()
            os.fsync(archivo.fileno())
        for intento in range(3):
            try:
                os.replace(temporal, ruta)
                break
            except PermissionError:
                if os.name != "nt" or intento == 2:
                    raise
                sleep(0.05 * (intento + 1))
    finally:
        if temporal is not None:
            temporal.unlink(missing_ok=True)


def guardar_catalogo(ruta, datos, limite=MAX_CATALOGO):
    validar_catalogo(datos)
    escribir_json(ruta, datos, limite, indentacion=2)


@contextmanager
def bloquear(ruta):
    ruta.parent.mkdir(parents=True, exist_ok=True)
    # Mantener el archivo evita que dos procesos bloqueen objetos distintos con el mismo nombre.
    with ruta.open("a+b") as archivo:
        try:
            if os.name == "nt":
                import msvcrt
                archivo.seek(0)
                msvcrt.locking(archivo.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                from fcntl import LOCK_EX, LOCK_NB, flock
                flock(archivo, LOCK_EX | LOCK_NB)
        except OSError as error:
            raise ValueError(f"hay otra operación en curso ({ruta.name})") from error
        yield


def cargar_o_crear(ruta):
    try:
        return leer_catalogo(ruta)
    except FileNotFoundError:
        return []


def resolver_catalogo(ruta):
    if ruta is not None:
        return ruta.resolve()
    load_dotenv(Path(__file__).with_name(".env"))
    configurada = os.getenv(VARIABLE)
    if not configurada:
        raise ValueError(f"configura {VARIABLE} o indica --catalogo")
    return (Path(__file__).resolve().parent / configurada).resolve()


def importar(ruta, args):
    identidad = _identidad(args.titulo, args.artista)
    if identidad is None or not all(clave_cancion(identidad)):
        raise ValueError("indica un título y un artista válidos")
    fragmentos = [extraer_fragmento(archivo) for archivo in args.archivos]
    with bloquear(ruta.with_suffix(ruta.suffix + ".lock")):
        datos = cargar_o_crear(ruta)
        clave = clave_cancion(identidad)
        existentes = [pista for pista in datos if clave_cancion(pista) == clave]
        if existentes:
            identidad = {campo: existentes[0][campo] for campo in identidad}
        agregados = 0
        for notas in fragmentos:
            if any(mismo_fragmento(pista["notas"], notas) for pista in existentes):
                continue
            if any(mismo_fragmento(pista["notas"], notas) for pista in datos):
                raise ValueError("un fragmento equivalente ya está registrado para otra canción")
            pista = dict(identidad, notas=notas)
            for campo in ("fuente", "licencia"):
                valor = getattr(args, campo)
                if valor:
                    pista[campo] = valor
            datos.append(pista)
            existentes.append(pista)
            agregados += 1
        if agregados:
            guardar_catalogo(ruta, datos)
        print(f"Fragmentos agregados: {agregados}. Duplicados omitidos: {len(fragmentos) - agregados}.")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalogo", type=Path, help=f"Ruta alternativa a {VARIABLE}")
    comandos = parser.add_subparsers(dest="comando", required=True)
    agregar = comandos.add_parser("importar", help="Agrega uno o varios fragmentos de una canción")
    agregar.add_argument("--titulo", required=True)
    agregar.add_argument("--artista", required=True)
    agregar.add_argument("--fuente", help="Procedencia o atribución de las grabaciones")
    agregar.add_argument("--licencia", help="Licencia o permiso de uso")
    agregar.add_argument("archivos", nargs="+", type=Path)
    comandos.add_parser("listar", help="Lista las canciones con su número de fragmentos")
    comandos.add_parser("estado", help="Valida el catálogo y muestra sus totales")
    administracion = comandos.add_parser("sincronizacion", help="Muestra fuentes, licencias y actividad")
    administracion.add_argument("--config", type=Path, help="Configuración alternativa")
    args = parser.parse_args(argv)
    try:
        ruta = resolver_catalogo(args.catalogo)
        if args.comando == "importar":
            importar(ruta, args)
        elif args.comando == "sincronizacion":
            from sincronizador_catalogo import CONFIGURACION, mostrar_estado
            mostrar_estado(ruta, args.config or CONFIGURACION)
        else:
            datos = leer_catalogo(ruta)
            canciones = {}
            for pista in datos:
                titulo, artista, cantidad = canciones.get(
                    clave_cancion(pista), (pista["cancion"], pista["artista"], 0)
                )
                canciones[clave_cancion(pista)] = titulo, artista, cantidad + 1
            if args.comando == "listar":
                for titulo, artista, cantidad in sorted(canciones.values(), key=lambda item: item[0].casefold()):
                    print(f"{titulo} — {artista} ({cantidad} fragmentos)")
            estado = "válido" if datos else "válido, vacío"
            print(f"Catálogo {estado}: {len(canciones)} canciones, {len(datos)} fragmentos de 500.\n{ruta}")
    except (OSError, ValueError, RecursionError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
