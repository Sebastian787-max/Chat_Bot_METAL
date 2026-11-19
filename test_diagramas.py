import unittest
from io import BytesIO

from PIL import Image

from diagramas import generar_diagramas, posicion_acorde


class PruebasDiagramas(unittest.TestCase):
    def test_cuatro_acordes_unicos_en_un_png_en_memoria(self):
        esperados = {
            "Em": (0, 2, 2, 0, 0, 0),
            "C": ("x", 3, 2, 0, 1, 0),
            "G": (3, 2, 0, 0, 0, 3),
            "D/F#": (2, "x", 0, 2, 3, 2),
        }
        for acorde, cuerdas in esperados.items():
            self.assertEqual(posicion_acorde(acorde).cuerdas, cuerdas)
        salida, omitidos = generar_diagramas([*esperados, "Em"])
        self.assertIsInstance(salida, BytesIO)
        self.assertEqual(omitidos, [])
        with salida:
            self.assertEqual(salida.tell(), 0)
            self.assertLess(len(salida.getvalue()), 1_000_000)
            with Image.open(salida) as imagen:
                self.assertEqual(imagen.format, "PNG")
                self.assertEqual(imagen.size, (768, 220))
                imagen.verify()

    def test_cejillas_y_enarmonicos(self):
        f = posicion_acorde("F#m")
        bb = posicion_acorde("Bb")
        self.assertEqual(f.cuerdas, (2, 4, 4, 2, 2, 2))
        self.assertEqual(f.cejilla, (2, 0, 5))
        self.assertEqual(bb.cuerdas, ("x", 1, 3, 3, 3, 1))
        self.assertEqual(bb.cejilla, (1, 1, 5))
        self.assertEqual(posicion_acorde("A#"), bb)
        self.assertEqual(posicion_acorde("Dbm7"), posicion_acorde("C#m7"))
        self.assertEqual(posicion_acorde("F5").cuerdas, (1, 3, 3, "x", "x", "x"))

    def test_omite_acorde_raro_y_limita_la_cuadricula_a_cuatro_columnas(self):
        imagen, omitidos = generar_diagramas(["C13b9", "C", "D", "E", "F", "G"])
        self.assertEqual(omitidos, ["C13b9"])
        with imagen, Image.open(imagen) as png:
            self.assertEqual(png.size, (768, 440))
        self.assertEqual(generar_diagramas(["C13b9", "C13b9"]), (None, ["C13b9"]))
