"""Diagramas de guitarra en afinación estándar."""

import re
from dataclasses import dataclass
from io import BytesIO

from PIL import Image, ImageDraw, ImageFont


# De Mi grave a Mi agudo. Las cuerdas al aire se marcan con 0.
POSICIONES_ABIERTAS = {
    "C": ("x", 3, 2, 0, 1, 0),
    "D": ("x", "x", 0, 2, 3, 2),
    "E": (0, 2, 2, 1, 0, 0),
    "G": (3, 2, 0, 0, 0, 3),
    "A": ("x", 0, 2, 2, 2, 0),
    "Am": ("x", 0, 2, 2, 1, 0),
    "Em": (0, 2, 2, 0, 0, 0),
    "Dm": ("x", "x", 0, 2, 3, 1),
    "E7": (0, 2, 0, 1, 0, 0),
    "A7": ("x", 0, 2, 0, 2, 0),
    "D7": ("x", "x", 0, 2, 1, 2),
    "G7": (3, 2, 0, 0, 0, 1),
    "C7": ("x", 3, 2, 3, 1, 0),
    "B7": ("x", 2, 1, 2, 0, 2),
    "Cmaj7": ("x", 3, 2, 0, 0, 0),
    "Fmaj7": ("x", "x", 3, 2, 1, 0),
    "Amaj7": ("x", 0, 2, 1, 2, 0),
    "Dmaj7": ("x", "x", 0, 2, 2, 2),
    "Am7": ("x", 0, 2, 0, 1, 0),
    "Em7": (0, 2, 2, 0, 3, 0),
    "Dm7": ("x", "x", 0, 2, 1, 1),
    "Asus2": ("x", 0, 2, 2, 0, 0),
    "Asus4": ("x", 0, 2, 2, 3, 0),
    "Dsus2": ("x", "x", 0, 2, 3, 0),
    "Dsus4": ("x", "x", 0, 2, 3, 3),
    "Esus4": (0, 2, 2, 2, 0, 0),
    "Cadd9": ("x", 3, 2, 0, 3, 3),
    "D/F#": (2, "x", 0, 2, 3, 2),
    "G/B": ("x", 2, 0, 0, 3, 3),
    "C/G": (3, 3, 2, 0, 1, 0),
}

_NOTAS = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
_ALTERACIONES = {"": 0, "#": 1, "b": -1}
_FORMAS = {
    "": (POSICIONES_ABIERTAS["E"], POSICIONES_ABIERTAS["A"]),
    "m": (POSICIONES_ABIERTAS["Em"], POSICIONES_ABIERTAS["Am"]),
    "7": (POSICIONES_ABIERTAS["E7"], POSICIONES_ABIERTAS["A7"]),
    "m7": ((0, 2, 0, 0, 0, 0), POSICIONES_ABIERTAS["Am7"]),
    "5": ((0, 2, 2, "x", "x", "x"), ("x", 0, 2, 2, "x", "x")),
}
_ACORDE_TRANSPONIBLE = re.compile(r"([A-G])([#b]?)(m7|m|7|5)?")
_ANCHO, _ALTO = 192, 220
_TRASTES = 5
_FONDO = "#14171c"
_CAJA = "#20252d"
_LINEAS = "#bac5d1"
_TEXTO = "#eef2f6"
_DEDOS = "#f0ad62"


@dataclass(frozen=True)
class Posicion:
    cuerdas: tuple[int | str, ...]
    cejilla: tuple[int, int, int] | None = None


def posicion_acorde(acorde):
    acorde = acorde.replace("♯", "#").replace("♭", "b")
    if acorde in POSICIONES_ABIERTAS:
        return Posicion(POSICIONES_ABIERTAS[acorde])
    partes = _ACORDE_TRANSPONIBLE.fullmatch(acorde)
    if not partes:
        return None
    nombre, alteracion, sufijo = partes.groups()
    nota = _NOTAS[nombre] + _ALTERACIONES[alteracion]
    trastes = [(nota - _NOTAS["E"]) % 12, (nota - _NOTAS["A"]) % 12]
    forma_a = int(trastes[1] < trastes[0])
    traste = trastes[forma_a]
    forma = _FORMAS[sufijo or ""][forma_a]
    cuerdas = tuple(traste + valor if isinstance(valor, int) else valor for valor in forma)
    cejilla = (traste, forma_a, 5) if traste and sufijo != "5" else None
    return Posicion(cuerdas, cejilla)


def _dibujar_posicion(dibujo, acorde, posicion, x, y, fuentes):
    titulo, texto = fuentes
    dibujo.rounded_rectangle((x + 5, y + 5, x + _ANCHO - 5, y + _ALTO - 5), radius=12, fill=_CAJA)
    dibujo.text((x + _ANCHO // 2, y + 26), acorde, font=titulo, fill=_TEXTO, anchor="mm")
    trastes = [valor for valor in posicion.cuerdas if isinstance(valor, int) and valor > 0]
    inicial = min(trastes) if max(trastes, default=0) > _TRASTES else 1
    izquierda, arriba = x + 46, y + 67
    paso_x, paso_y = 20, 23
    xs = [izquierda + cuerda * paso_x for cuerda in range(6)]
    abajo = arriba + _TRASTES * paso_y
    for xc in xs:
        dibujo.line((xc, arriba, xc, abajo), fill=_LINEAS, width=1)
    for division in range(_TRASTES + 1):
        yc = arriba + division * paso_y
        dibujo.line((xs[0], yc, xs[-1], yc), fill=_LINEAS, width=4 if not division and inicial == 1 else 1)
    if inicial != 1:
        dibujo.text((izquierda - 10, arriba + paso_y // 2), str(inicial), font=texto, fill=_TEXTO, anchor="rm")

    if posicion.cejilla:
        traste, desde, hasta = posicion.cejilla
        yc = arriba + (traste - inicial) * paso_y + paso_y // 2
        dibujo.rounded_rectangle((xs[desde] - 6, yc - 5, xs[hasta] + 6, yc + 5), radius=5, fill=_DEDOS)
    for xc, valor in zip(xs, posicion.cuerdas):
        if valor in ("x", 0):
            dibujo.text((xc, arriba - 13), "x" if valor == "x" else "o", font=texto, fill=_TEXTO, anchor="mm")
        else:
            yc = arriba + (valor - inicial) * paso_y + paso_y // 2
            dibujo.ellipse((xc - 6, yc - 6, xc + 6, yc + 6), fill=_DEDOS)
    digitacion = "-".join(map(str, posicion.cuerdas))
    dibujo.text((x + _ANCHO // 2, y + 198), digitacion, font=texto, fill=_TEXTO, anchor="mm")


def generar_diagramas(acordes):
    """Devuelve el PNG en memoria y los acordes únicos sin posición conocida."""
    dibujables, omitidos = [], []
    for acorde in dict.fromkeys(acordes):
        posicion = posicion_acorde(acorde)
        if posicion:
            dibujables.append((acorde, posicion))
        else:
            omitidos.append(acorde)
    if not dibujables:
        return None, omitidos

    columnas = min(4, len(dibujables))
    filas = (len(dibujables) + columnas - 1) // columnas
    with Image.new("RGB", (columnas * _ANCHO, filas * _ALTO), _FONDO) as imagen:
        dibujo = ImageDraw.Draw(imagen)
        fuentes = ImageFont.load_default(size=24), ImageFont.load_default(size=15)
        for indice, (acorde, posicion) in enumerate(dibujables):
            fila, columna = divmod(indice, columnas)
            _dibujar_posicion(dibujo, acorde, posicion, columna * _ANCHO, fila * _ALTO, fuentes)
        salida = BytesIO()
        imagen.save(salida, format="PNG", optimize=True)
    salida.seek(0)
    return salida, omitidos
