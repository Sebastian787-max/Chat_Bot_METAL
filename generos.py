"""Reconoce géneros e intenciones musicales en mensajes."""

import re
from unicodedata import normalize


GENEROS = {
    "rock": (),
    "hard rock": ("rock duro", "rock pesado"),
    "heavy metal": ("metal pesado",),
    "thrash metal": ("thrash", "metal thrash"),
    "death metal": ("metal de muerte", "metal de la muerte"),
    "black metal": ("metal negro",),
    "power metal": ("metal épico",),
    "doom metal": ("metal doom",),
    "progressive metal": ("prog metal", "metal progresivo"),
    "folk metal": ("metal folk", "metal folclórico", "metal folklórico"),
    "symphonic metal": ("metal sinfónico",),
    "gothic metal": ("metal gótico",),
    "groove metal": ("metal groove",),
    "metalcore": ("metal core",),
    "nu metal": ("new metal", "nü metal"),
    "industrial metal": ("metal industrial",),
    "punk": (),
    "punk rock": ("rock punk",),
    "grunge": (),
    "alternative rock": ("rock alternativo",),
    "classic rock": ("rock clásico",),
    "glam rock": ("rock glam",),
    "stoner rock": ("rock stoner",),
    "blues rock": ("rock blues",),
    "progressive rock": ("prog rock", "rock progresivo"),
    "post-rock": ("post rock",),
    "hardcore": ("hardcore punk", "punk hardcore"),
}


def normalizar(texto):
    sin_tildes = "".join(c for c in normalize("NFD", texto.casefold()) if not "\u0300" <= c <= "\u036f")
    return " ".join(re.findall(r"\w+", sin_tildes))


_ETIQUETAS = {
    normalizar(nombre): etiqueta
    for etiqueta, sinonimos in GENEROS.items()
    for nombre in (etiqueta, *sinonimos)
}
_PATRON_GENEROS = re.compile(
    r"\b(?:" + "|".join(map(re.escape, sorted(_ETIQUETAS, key=len, reverse=True))) + r")\b"
)
_PATRON_INTENCION = re.compile(
    r"\b(?:recomienda|recomiendame|recomendar|recomiendas|quiero conocer|quiero escuchar|"
    r"algo de|algo parecido a|bandas? de|musica de|sugiere|sugiereme|que escucho)\b"
)
_PATRON_PARECIDAS = re.compile(r"^(?:(?:algo )?parecido a|similar(?:es)? a|como)\s+(.+)$", re.IGNORECASE)
_RELLENO_BANDA = re.compile(
    r"^(?:quiero\s+saber\s+de|quiero\s+conocer|cu[eé]ntame\s+de|h[aá]blame\s+de|"
    r"info\s+de|qu[eé]\s+sabes\s+de)\s+",
    re.IGNORECASE,
)


def detectar_genero(texto):
    coincidencias = _PATRON_GENEROS.findall(normalizar(texto))
    return _ETIQUETAS[max(coincidencias, key=len)] if coincidencias else None


def es_nombre_de_genero(texto):
    return normalizar(texto) in _ETIQUETAS


def detectar_intencion_recomendacion(texto):
    return bool(_PATRON_INTENCION.search(normalizar(texto)))


def detectar_referencia_similar(texto):
    coincidencia = _PATRON_PARECIDAS.match(texto.strip(" ¿¡"))
    return coincidencia[1].rstrip(" ?!.,") if coincidencia else None


def limpiar_nombre_banda(texto):
    consulta = texto.strip(" ¿¡")
    prefijo = _RELLENO_BANDA.match(consulta)
    return consulta[prefijo.end():].strip(" ¿¡?!") if prefijo else texto
