"""Lee partituras acotadas y selecciona una línea melódica sustentada por el archivo."""

from bisect import bisect_right
from collections import defaultdict
from dataclasses import dataclass
from io import BytesIO
from math import isfinite
from statistics import median
from xml.etree.ElementTree import ParseError
from zipfile import BadZipFile, ZipFile
import re

from defusedxml.ElementTree import fromstring
from defusedxml.common import DefusedXmlException
from mido import MidiFile

from catalogo_melodias import normalizar_fragmento, mismo_fragmento
from generos import normalizar


MAX_ARCHIVO = 2 * 1024 * 1024
MAX_XML = 6 * 1024 * 1024
MAX_SEGUNDOS = 600
ETIQUETA_MELODICA = re.compile(
    r"\b(?:voice|vocal|vocals|voix|stimme|gesang|singstimme|melody|melodia|canto|"
    r"soprano|mezzo|tenor|baritone|lead)\b"
)
UNIDADES_TEMPO = {"whole": 4, "half": 2, "quarter": 1, "eighth": 0.5, "16th": 0.25}


@dataclass(frozen=True)
class Partitura:
    fragmentos: list
    titulo: str = ""
    compositor: str = ""
    derechos: tuple = ()


def reloj(tempos, inicial):
    cambios = sorted({0: inicial, **tempos}.items())
    posiciones = [posicion for posicion, _ in cambios]
    segundos = [0.0]
    for (posicion, escala), (siguiente, _) in zip(cambios, cambios[1:]):
        segundos.append(segundos[-1] + (siguiente - posicion) * escala)

    def convertir(posicion):
        indice = bisect_right(posiciones, posicion) - 1
        return segundos[indice] + (posicion - posiciones[indice]) * cambios[indice][1]

    return convertir


def leer_midi(datos):
    if not datos.startswith(b"MThd"):
        raise ValueError("cabecera MIDI inválida")
    midi = MidiFile(file=BytesIO(datos))
    if midi.type not in (0, 1) or midi.ticks_per_beat <= 0 or len(midi.tracks) > 128:
        raise ValueError("formato MIDI o resolución temporal no admitidos")
    pistas, tempos = [], {}
    fin = 0
    for pista in midi.tracks:
        tick, abiertas = 0, {}
        canales = defaultdict(list)
        for mensaje in pista:
            tick += mensaje.time
            if mensaje.type == "set_tempo":
                if mensaje.tempo <= 0 or tempos.get(tick, mensaje.tempo) != mensaje.tempo:
                    raise ValueError("mapa de tempo MIDI inválido o contradictorio")
                tempos[tick] = mensaje.tempo
            if mensaje.type not in ("note_on", "note_off") or mensaje.channel == 9:
                continue
            clave = mensaje.channel, mensaje.note
            if mensaje.type == "note_on" and mensaje.velocity:
                if clave in abiertas:
                    raise ValueError("notas MIDI superpuestas de igual altura")
                abiertas[clave] = tick
            elif clave in abiertas:
                inicio = abiertas.pop(clave)
                if tick > inicio:
                    canales[mensaje.channel].append((inicio, tick, mensaje.note))
        if abiertas:
            raise ValueError("hay notas MIDI sin cerrar")
        pistas.extend((pista.name, False, notas) for notas in canales.values())
        fin = max(fin, tick)
    convertir = reloj({t: v / (1e6 * midi.ticks_per_beat) for t, v in tempos.items()},
                      0.5 / midi.ticks_per_beat)
    return pistas, convertir, fin


def xml_seguro(datos):
    raiz = fromstring(datos, forbid_entities=True, forbid_external=True)
    for elemento in raiz.iter():
        elemento.tag = elemento.tag.rsplit("}", 1)[-1]
    return raiz


def leer_xml(datos, comprimido):
    if comprimido:
        with ZipFile(BytesIO(datos)) as archivo:
            miembros = archivo.infolist()
            if len(miembros) > 32 or sum(i.file_size for i in miembros) > MAX_XML:
                raise ValueError("MusicXML comprimido excede los límites")
            if len({item.filename for item in miembros}) != len(miembros):
                raise ValueError("MusicXML comprimido con rutas duplicadas")
            contenedor = xml_seguro(archivo.read("META-INF/container.xml"))
            ruta = contenedor.find("./rootfiles/rootfile")
            if ruta is None or not ruta.get("full-path"):
                raise ValueError("MusicXML comprimido sin partitura")
            datos = archivo.read(ruta.get("full-path"))
    raiz = xml_seguro(datos)
    if raiz.tag != "score-partwise":
        raise ValueError("se requiere MusicXML score-partwise")
    nombres = {
        parte.get("id"): parte.findtext("part-name", "")
        for parte in raiz.findall("./part-list/score-part")
        if parte.find(".//midi-unpitched") is None
    }
    pistas, tempos = [], {}
    fin = 0
    for parte in raiz.findall("part"):
        identificador = parte.get("id")
        if identificador not in nombres:
            continue
        notas, letras, final = leer_parte(parte, tempos)
        pistas.extend((nombres[identificador], voz in letras, eventos) for voz, eventos in notas.items())
        fin = max(fin, final)
    identificacion = raiz.find("identification")
    compositores = () if identificacion is None else identificacion.findall("creator[@type='composer']")
    compositor = " / ".join(c.text.strip() for c in compositores if c.text and c.text.strip())
    derechos = tuple(n.text.strip() for n in raiz.findall("./identification/rights") if n.text and n.text.strip())
    titulo = raiz.findtext("movement-title") or raiz.findtext("work/work-title", "")
    return pistas, reloj(tempos, 0.5), fin, (titulo, compositor, derechos)


def tempo_musicxml(elemento):
    sonido = elemento if elemento.tag == "sound" else elemento.find("sound")
    if sonido is not None and sonido.get("tempo"):
        return float(sonido.get("tempo"))
    marca = elemento.find("./direction-type/metronome")
    if marca is None:
        return None
    unidad = UNIDADES_TEMPO.get(marca.findtext("beat-unit"))
    ritmo = marca.findtext("per-minute", "")
    if unidad is None or not re.fullmatch(r"\d+(?:\.\d+)?", ritmo):
        return None
    puntos = len(marca.findall("beat-unit-dot"))
    return float(ritmo) * unidad * (2 - 0.5 ** puntos)


def leer_parte(parte, tempos):
    posicion, divisiones, transporte = 0.0, 1, 0
    notas, ligaduras, letras = defaultdict(list), {}, set()
    for compas in parte.findall("measure"):
        inicio_compas = maximo = posicion
        ataque = posicion
        for elemento in compas:
            if elemento.tag == "attributes":
                divisiones = int(elemento.findtext("divisions", str(divisiones)))
                transposicion = elemento.find("transpose")
                if transposicion is not None:
                    transporte = int(transposicion.findtext("chromatic", "0"))
                    transporte += 12 * int(transposicion.findtext("octave-change", "0"))
                if divisiones <= 0:
                    raise ValueError("divisiones MusicXML inválidas")
            elif elemento.tag in ("direction", "sound"):
                bpm = tempo_musicxml(elemento)
                if bpm is not None:
                    if not isfinite(bpm) or not 10 <= bpm <= 500:
                        raise ValueError("tempo MusicXML inválido")
                    desplazamiento = float(elemento.findtext("offset", "0")) / divisiones
                    if not isfinite(desplazamiento):
                        raise ValueError("desplazamiento de tempo inválido")
                    instante = max(0.0, posicion + desplazamiento)
                    tempos[round(instante, 8)] = 60 / bpm
            elif elemento.tag in ("note", "backup", "forward"):
                duracion = int(elemento.findtext("duration", "0")) / divisiones
                if duracion < 0:
                    raise ValueError("duración MusicXML inválida")
                if elemento.tag == "backup":
                    posicion -= duracion
                    if posicion < inicio_compas - 1e-7:
                        raise ValueError("el cursor MusicXML sale del compás")
                    posicion = max(posicion, inicio_compas)
                    continue
                if elemento.tag == "forward":
                    posicion += duracion
                    maximo = max(maximo, posicion)
                    continue
                if elemento.find("grace") is not None:
                    continue
                if elemento.find("chord") is None:
                    ataque = posicion
                    posicion += duracion
                final = ataque + duracion
                maximo = max(maximo, final)
                altura = elemento.find("pitch")
                if altura is None or not duracion:
                    continue
                grado = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}[altura.findtext("step")]
                alteracion = float(altura.findtext("alter", "0"))
                if not alteracion.is_integer():
                    raise ValueError("altura microtonal fuera del formato del catálogo")
                midi = 12 * (1 + int(altura.findtext("octave"))) + grado + int(alteracion) + transporte
                if not 0 <= midi <= 127:
                    raise ValueError("altura MusicXML fuera del rango MIDI")
                voz = elemento.findtext("staff", "1"), elemento.findtext("voice", "1")
                if elemento.find("lyric") is not None:
                    letras.add(voz)
                clave = voz, midi
                tipos = {tie.get("type") for tie in elemento.findall("tie")}
                anterior = ligaduras.pop(clave, ataque) if "stop" in tipos else ataque
                if "start" in tipos:
                    ligaduras[clave] = anterior
                else:
                    notas[voz].append((anterior, final, midi))
        posicion = maximo
    if ligaduras:
        raise ValueError("hay ligaduras MusicXML sin cerrar")
    return notas, letras, posicion


def frases(notas, convertir):
    corridas, corrida = [], []
    fin = 0.0
    for ataque, salida, midi in sorted(notas):
        inicio, final = convertir(ataque), convertir(salida)
        if inicio < fin - 1e-6:
            return []
        if inicio - fin > 1.0 or final - inicio < 0.07:
            corridas.append(corrida)
            corrida = []
        if final - inicio >= 0.07:
            corrida.append((midi, final - inicio))
        fin = final
    corridas.append(corrida)
    resultado = []
    for corrida in corridas:
        for inicio in range(0, len(corrida) - 7, 9):
            ventana = corrida[inicio:inicio + 17]
            while ventana and sum(duracion for _, duracion in ventana) > 30:
                ventana = ventana[:-1]
            try:
                notas_relativas = normalizar_fragmento(ventana)
            except ValueError:
                continue
            if not any(mismo_fragmento(notas_relativas, existente) for existente in resultado):
                resultado.append(notas_relativas)
            if len(resultado) == 12:
                return resultado
    return resultado


def extraer_partitura(datos, extension):
    if not 0 < len(datos) <= MAX_ARCHIVO:
        raise ValueError("partitura vacía o demasiado grande")
    try:
        metadatos = ()
        if extension in (".mid", ".midi"):
            pistas, convertir, fin = leer_midi(datos)
        elif extension in (".musicxml", ".xml", ".mxl"):
            pistas, convertir, fin, metadatos = leer_xml(datos, extension == ".mxl")
        else:
            raise ValueError("formato de partitura no admitido")
        if convertir(fin) > MAX_SEGUNDOS:
            raise ValueError("duración de la partitura superior a 10 minutos")
    except (BadZipFile, DefusedXmlException, EOFError, KeyError, OSError, ParseError,
            RuntimeError, TypeError, OverflowError, IndexError) as error:
        raise ValueError("partitura corrupta o estructura no admitida") from error
    candidatas = []
    indicadas = []
    for nombre, tiene_letra, notas in pistas:
        fragmentos = frases(notas, convertir)
        if tiene_letra or ETIQUETA_MELODICA.search(normalizar(nombre)):
            indicadas.append(fragmentos)
        if fragmentos:
            candidatas.append(fragmentos)
    # Si hay indicios explícitos, una voz inválida no convierte al acompañamiento en melodía.
    opciones = indicadas or candidatas
    if len(opciones) != 1 or not opciones[0]:
        raise ValueError("melodía ausente, polifónica o ambigua")
    return Partitura(opciones[0], *metadatos)
