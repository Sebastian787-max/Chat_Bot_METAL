"""Comprueba una partitura contra el reconocedor acústico antes de indexarla."""

import numpy as np

from reconocimiento_audio import TASA_AUDIO, extraer_melodia, preparar_audio, puntuar_fragmento


def renderizar_linea(notas, tono, pulso):
    partes = []
    for indice, (nota, duracion) in enumerate(notas):
        muestras = max(2, round(duracion * pulso * TASA_AUDIO))
        tiempo = np.arange(muestras) / TASA_AUDIO
        frecuencia = 440 * 2 ** ((nota + tono - 69) / 12)
        fase = 2 * np.pi * frecuencia * tiempo
        borde = np.minimum(1, np.minimum(tiempo, tiempo[-1] - tiempo) * 80)
        partes.append(9000 * (np.sin(fase) + 0.14 * np.sin(2 * fase)) * borde)
        if indice + 1 < len(notas) and nota == notas[indice + 1][0]:
            partes.append(np.zeros(round(0.08 * TASA_AUDIO)))
    return np.concatenate(partes)


def validar_reconocible(notas):
    if sum(duracion for _, duracion in notas) * 0.34 > 25:
        return False
    referencia = np.asarray(notas, dtype=float)
    for tono, pulso in ((4, 0.26), (-3, 0.34)):
        segmento, _ = preparar_audio(None, renderizar_linea(notas, tono, pulso))
        if segmento is None:
            return False
        extraida = np.asarray(extraer_melodia(segmento), dtype=float)
        if puntuar_fragmento(extraida, referencia) < 0.87:
            return False
    return True
