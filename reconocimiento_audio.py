"""Preparación de audio, consulta de huellas y búsqueda local por melodía."""

import json
import logging
import os
import shutil
import subprocess
import wave
from functools import lru_cache
from io import BytesIO
from pathlib import Path
from threading import Lock
from time import monotonic

import numpy as np
import requests

from generos import normalizar
from gemini_operaciones import OPERACION


TASA_AUDIO = 16000
_MAX_CLIP = 30
_HUELLA_LOCK = Lock()
_PROXIMA_HUELLA = 0.0


def diagnostico_audio(metodo, estado, *, nivel=logging.INFO):
    operacion = OPERACION.get()
    logger = logging.getLogger("audio")
    logger.log(nivel, "Audio op=%s método=%s estado=%s",
               operacion.id if operacion else "-", metodo, estado)


def decodificar_audio(datos, *, formato=None):
    try:
        if formato not in (None, "wav"):
            raise wave.Error
        with wave.open(BytesIO(datos)) as wav:
            canales, ancho, tasa, frames, *_ = wav.getparams()
            if ancho == 2 and 8000 <= tasa <= 192000 and 1 <= canales <= 8 and frames <= tasa * 61:
                pcm = np.frombuffer(wav.readframes(frames), dtype="<i2")
                if len(pcm) and len(pcm) % canales == 0 and (formato is None or len(pcm) == frames * canales):
                    pcm = pcm.reshape(-1, canales).mean(axis=1)
                    diagnostico_audio("decodificador", "wav_pcm_correcto")
                    return np.interp(np.arange(0, len(pcm), tasa / TASA_AUDIO), np.arange(len(pcm)), pcm)
    except (wave.Error, EOFError):
        pass

    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        diagnostico_audio("decodificador", "ffmpeg_no_disponible")
        return None
    entrada = ["-xerror", "-f", formato] if formato else []
    try:
        salida = subprocess.run(
            [ffmpeg, "-nostdin", "-loglevel", "error", "-protocol_whitelist", "pipe",
             *entrada, "-i", "pipe:0", "-map", "0:a:0", "-t", "61", "-ac", "1", "-ar", str(TASA_AUDIO),
             "-f", "s16le", "pipe:1"],
            input=datos, capture_output=True, timeout=5, check=True,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        diagnostico_audio("decodificador", "ffmpeg_fallo", nivel=logging.WARNING)
        return None
    if not salida or len(salida) % 2:
        diagnostico_audio("decodificador", "ffmpeg_salida_invalida", nivel=logging.WARNING)
        return None
    diagnostico_audio("decodificador", "ffmpeg_correcto")
    return np.frombuffer(salida, dtype="<i2").astype(float)


def preparar_audio(datos, pcm=None):
    """Devuelve el segmento útil y si el audio decodificado carece de señal útil."""
    if pcm is None:
        pcm = decodificar_audio(datos)
    if pcm is None:
        return None, False
    pcm = pcm - np.median(pcm)
    # Atenuamos retumbe subgrave si destaca. El resto del espectro se conserva.
    bins = int(32 * len(pcm) / TASA_AUDIO) + 1
    if bins > 1:
        espectro = np.fft.rfft(pcm)
        graves = espectro[:bins]
        if np.linalg.norm(graves) > 0.14 * np.linalg.norm(espectro):
            frecuencias = np.arange(bins) * TASA_AUDIO / len(pcm)
            graves *= 0.15 + 0.85 * np.clip((frecuencias - 16) / 16, 0, 1)
            pcm = np.fft.irfft(espectro, n=len(pcm))
    bloque = TASA_AUDIO // 10
    niveles = np.array([
        np.sqrt(np.mean(ventana ** 2))
        for ventana in np.array_split(pcm, max(1, len(pcm) // bloque))
    ])
    pico = float(np.max(niveles))
    piso = float(np.percentile(niveles, 20))
    # Un piso cercano al pico también puede ser música continua; conservamos esa señal.
    umbral = 2.3 * piso if piso < pico * 0.25 else pico * 0.10
    activos = np.flatnonzero(niveles > max(4, umbral, pico * 0.025))
    if not activos.size:
        return None, True

    inicio, fin = max(0, activos[0] - 2), min(len(niveles), activos[-1] + 3)
    ancho = _MAX_CLIP * 10
    if fin - inicio > ancho:
        # Contar bloques activos evita que un solo golpe fuerte determine el recorte.
        util = np.isin(np.arange(inicio, fin), activos).astype(int)
        inicio += int(np.argmax(np.convolve(util, np.ones(ancho, dtype=int), mode="valid")))
        fin = inicio + ancho
    bordes = np.linspace(0, len(pcm), len(niveles) + 1, dtype=int)
    segmento = pcm[bordes[inicio]:bordes[fin]]
    amplitud = np.max(np.abs(segmento))
    ganancia = min(12, (0.85 * 32767) / amplitud)
    return (segmento * ganancia).astype("<i2"), False


def como_wav(pcm):
    salida = BytesIO()
    with wave.open(salida, "wb") as wav:
        wav.setparams((1, 2, TASA_AUDIO, 0, "NONE", ""))
        wav.writeframes(pcm.tobytes())
    return salida.getvalue()


def extraer_melodia(pcm, *, cobertura_minima=0):
    """Altura monofónica por autocorrelación normalizada y ritmo por duración."""
    pcm = pcm[::2]
    tasa = TASA_AUDIO // 2
    marco, salto = 640, 160
    if len(pcm) < marco:
        return []
    ventanas = np.lib.stride_tricks.sliding_window_view(pcm, marco)[::salto].astype(float)
    ventanas -= ventanas.mean(axis=1, keepdims=True)
    energia = np.sum(ventanas ** 2, axis=1)
    espectro = np.fft.rfft(ventanas, n=2 * marco, axis=1)
    correlacion = np.fft.irfft(abs(espectro) ** 2, axis=1)
    minimo, maximo = 3, 205  # Aproximadamente 39 Hz a 2 kHz.
    periodos = np.arange(minimo, maximo + 1)
    acumulada = np.pad(np.cumsum(ventanas ** 2, axis=1), ((0, 0), (1, 0)))
    norma = np.sqrt(acumulada[:, marco - periodos] * (acumulada[:, [-1]] - acumulada[:, periodos]))
    correlacion = correlacion[:, minimo:maximo + 1] / np.maximum(norma, 1)
    notas = []
    anterior, duracion = None, 0
    umbral = max(energia.max() * 0.001, marco * 16)
    for curva, nivel in zip(correlacion, energia):
        valor = None
        if nivel > umbral:
            picos = np.flatnonzero((curva[1:-1] > curva[:-2]) & (curva[1:-1] >= curva[2:])) + 1
            if picos.size:
                fuertes = picos[curva[picos] >= max(0.68, np.max(curva[picos]) * 0.91)]
                if fuertes.size:
                    i = fuertes[0]
                    izq, medio, der = curva[i - 1:i + 2]
                    periodo = minimo + i + 0.5 * (izq - der) / (izq - 2 * medio + der)
                    valor = round(69 + 12 * np.log2(tasa / periodo / 440))
        if valor == anterior:
            duracion += 1
        else:
            if anterior is not None and duracion >= 4:
                notas.append((anterior, duracion))
            anterior, duracion = valor, 1
    if anterior is not None and duracion >= 4:
        notas.append((anterior, duracion))
    if sum(duracion for _, duracion in notas) < cobertura_minima * np.count_nonzero(energia > umbral):
        return []
    return notas


@lru_cache(maxsize=2)
def _leer_catalogo(ruta, _version):
    return validar_catalogo(leer_datos_catalogo(ruta))


def leer_datos_catalogo(ruta):
    with open(ruta, encoding="utf-8") as archivo:
        contenido = archivo.read(2_000_001)
    if len(contenido) > 2_000_000:
        raise ValueError("Catálogo de melodías demasiado grande")
    return json.loads(contenido)


def validar_catalogo(datos):
    if not isinstance(datos, list) or len(datos) > 500:
        raise ValueError("Catálogo de melodías inválido")
    catalogo = []
    for pista in datos:
        if not isinstance(pista, dict):
            raise ValueError("Entrada de catálogo inválida")
        nombre = _identidad(pista.get("cancion"), pista.get("artista"))
        notas = pista.get("notas")
        if not nombre or not isinstance(notas, list) or not 8 <= len(notas) <= 128:
            raise ValueError("Entrada de catálogo inválida")
        if any(not isinstance(nota, list) or len(nota) != 2
               or type(nota[0]) is not int or not 0 <= nota[0] <= 127
               or type(nota[1]) not in (float, int) or not 0.01 <= nota[1] <= 100
               for nota in notas):
            raise ValueError("Notas de catálogo inválidas")
        if pista.get("rol_artista") == "compositor":
            nombre["rol_artista"] = "compositor"
        catalogo.append((nombre, np.asarray(notas, dtype=float)))
    return catalogo


def ruta_catalogo_melodias():
    ruta = os.getenv("MOSHPIT_CATALOGO_MELODIAS")
    return Path(__file__).resolve().parent / ruta if ruta else None


def version_catalogo(ruta):
    if ruta is None:
        return None
    try:
        estado = ruta.stat()
    except OSError:
        return None
    return ruta, estado.st_ino, estado.st_mtime_ns, estado.st_ctime_ns, estado.st_size


def puntuar_fragmento(consulta, notas):
    if len(consulta) < 8 or len(notas) < 8:
        return 0
    ancho = min(len(consulta) - 1, len(notas) - 1, 16)
    referencia = np.lib.stride_tricks.sliding_window_view(np.diff(notas[:, 0]), ancho)
    ritmo_ref = np.lib.stride_tricks.sliding_window_view(np.log(notas[:-1, 1]), ancho)
    mejor = 0
    total = len(consulta) - ancho
    inicios = np.linspace(0, total - 1, min(24, total), dtype=int)
    for inicio in inicios:
        segmento = consulta[inicio:inicio + ancho + 1]
        intervalos = np.diff(segmento[:, 0])
        if len(set(intervalos)) < 3 or np.count_nonzero(intervalos) < 5:
            continue
        error = np.abs(referencia - intervalos)
        altura = (np.mean(error == 0, axis=1) + np.mean(error <= 1, axis=1)) / 2
        ritmo = np.log(segmento[:-1, 1]) - ritmo_ref
        ritmo -= np.median(ritmo, axis=1, keepdims=True)
        regularidad = np.mean(abs(ritmo) < np.log(1.7), axis=1)
        mejor = max(mejor, float(np.max(0.85 * altura + 0.15 * regularidad)))
    return mejor


def buscar_melodia(pcm):
    ruta = ruta_catalogo_melodias()
    if ruta is None:
        diagnostico_audio("melodia", "catalogo_no_configurado")
        return []
    if pcm is None:
        diagnostico_audio("melodia", "audio_no_decodificado")
        return []
    try:
        version = version_catalogo(ruta)
        if version is None:
            raise OSError("catálogo inaccesible")
        catalogo = _leer_catalogo(ruta, version)
    except (OSError, ValueError, RecursionError):
        diagnostico_audio("melodia", "catalogo_invalido_o_inaccesible", nivel=logging.WARNING)
        return []
    if not catalogo:
        diagnostico_audio("melodia", "catalogo_vacio")
        return []
    consulta = np.asarray(extraer_melodia(pcm), dtype=float)
    if len(consulta) < 8:
        diagnostico_audio("melodia", "notas_insuficientes")
        return []
    opciones = _ordenar((puntuar_fragmento(consulta, notas), pista) for pista, notas in catalogo)
    diagnostico_audio("melodia", "candidato" if opciones[0][0] >= 0.75 else "sin_coincidencias")
    return opciones


def _identidad(cancion, artista):
    if all(isinstance(campo, str) and campo.strip() for campo in (cancion, artista)):
        return {"cancion": " ".join(cancion.split())[:150], "artista": " ".join(artista.split())[:150]}
    return None


def _ordenar(clasificados):
    mejores = {}
    for valor, pista in clasificados:
        clave = normalizar(pista["cancion"]), normalizar(pista["artista"])
        if clave not in mejores or valor > mejores[clave][0]:
            mejores[clave] = valor, pista
    return sorted(mejores.values(), key=lambda par: par[0], reverse=True)


def buscar_huella(datos):
    """Consulta opcional de AcoustID. Solo transmite huella y duración."""
    global _PROXIMA_HUELLA
    clave = os.getenv("ACOUSTID_API_KEY")
    if not clave:
        diagnostico_audio("acoustid", "clave_no_configurada")
        return []
    fpcalc = shutil.which("fpcalc")
    if not fpcalc:
        diagnostico_audio("acoustid", "fpcalc_no_disponible", nivel=logging.WARNING)
        return []
    salida = subprocess.run(
        [fpcalc, "-length", "61", "-chunk", "65", "-json", "-"], input=datos, capture_output=True, timeout=6,
    )
    # fpcalc puede emitir una huella válida al encontrar EOF en stdin.
    huella = json.loads(salida.stdout)
    if not huella.get("fingerprint") or not 8 <= huella["duration"] <= 61:
        diagnostico_audio("acoustid", "huella_insuficiente")
        return []
    with _HUELLA_LOCK:
        ahora = monotonic()
        if ahora < _PROXIMA_HUELLA:
            diagnostico_audio("acoustid", "pausa_por_cuota_o_frecuencia")
            return []
        _PROXIMA_HUELLA = ahora + 0.4
    respuesta = requests.post(
        "https://api.acoustid.org/v2/lookup",
        data={"client": clave, "duration": round(huella["duration"]),
              "fingerprint": huella["fingerprint"], "meta": "recordings", "format": "json"},
        timeout=(2, 4), allow_redirects=False,
    )
    if respuesta.status_code == 429:
        with _HUELLA_LOCK:
            _PROXIMA_HUELLA = max(_PROXIMA_HUELLA, monotonic() + 60)
    respuesta.raise_for_status()
    cuerpo = respuesta.json()
    if cuerpo.get("status") != "ok":
        raise ValueError("AcoustID rechazó la consulta")
    clasificados = []
    for resultado in cuerpo.get("results", []):
        valor = resultado.get("score")
        if type(valor) not in (float, int) or not 0 <= valor <= 1:
            continue
        grabaciones = resultado.get("recordings") or []
        if not grabaciones:
            # Una huella fuerte sin metadatos también puede volver ambiguo el resultado.
            clasificados.append((valor, {"cancion": "", "artista": ""}))
        for grabacion in grabaciones:
            artistas = grabacion.get("artists") or []
            nombre = " & ".join(a["name"] for a in artistas if isinstance(a.get("name"), str))
            pista = _identidad(grabacion.get("title"), nombre)
            clasificados.append((valor, pista or {"cancion": "", "artista": ""}))
    diagnostico_audio("acoustid", "candidato" if clasificados else "sin_coincidencias")
    return _ordenar(clasificados)

