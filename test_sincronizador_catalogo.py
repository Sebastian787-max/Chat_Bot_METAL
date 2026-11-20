"""Pruebas sin red del índice, las partituras y el reconocimiento tras sincronizar."""

import hashlib
import json
import os
import unittest
from contextlib import redirect_stdout
from io import BytesIO, StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, Mock, patch
from zipfile import ZipFile

import requests
from mido import Message, MetaMessage, MidiFile, MidiTrack

import audio
import bot
import catalogo_melodias
import estilos
from fuentes_melodicas import CC0, Descargas, ErrorFuente, openscore
from procesador_midi import extraer_partitura
from sincronizador_catalogo import cargar_registro, configuracion, ruta_registro, sincronizar
from test_reconocimiento_audio import _NOTAS, _RITMO, sintetizar
from validador_melodias import validar_reconocible


def midi(tono=0, tempo=500000, cambiar=False, ambiguo=False):
    archivo = MidiFile()
    tempos = MidiTrack([MetaMessage("set_tempo", tempo=tempo)])
    if cambiar:
        tempos.append(MetaMessage("set_tempo", tempo=tempo * 2, time=960))
    archivo.tracks.append(tempos)
    pistas = [("Melody" if not ambiguo else "Instrumento A", 0), ("Batería", 9)]
    if ambiguo:
        pistas.append(("Instrumento B", 1))
    for nombre, canal in pistas:
        pista = MidiTrack([MetaMessage("track_name", name=nombre)])
        for nota, ritmo in zip(_NOTAS, _RITMO):
            pista.extend([
                Message("note_on", note=nota + tono, velocity=75, channel=canal),
                Message("note_off", note=nota + tono, time=round(480 * ritmo), channel=canal),
            ])
        archivo.tracks.append(pista)
    salida = BytesIO()
    archivo.save(file=salida)
    return salida.getvalue()


def musicxml(cambiar=False):
    grados = "C C D D E F F G G A A B".split()
    notas = []
    for indice, (nota, ritmo) in enumerate(zip(_NOTAS, _RITMO)):
        if cambiar and indice == 4:
            notas.append('<direction><sound tempo="60"/></direction>')
        grado = grados[nota % 12]
        alteracion = int(nota % 12 in (1, 3, 6, 8, 10))
        notas.append(
            f"<note><pitch><step>{grado}</step><alter>{alteracion}</alter>"
            f"<octave>{nota // 12 - 1}</octave></pitch><duration>{round(100 * ritmo)}</duration></note>"
        )
    return (
        '<score-partwise><movement-title>Frase original</movement-title>'
        '<identification><creator type="composer">Autor de prueba</creator><rights>OpenScore (CC0)</rights>'
        '</identification><part-list><score-part id="P1"><part-name>Voice</part-name></score-part></part-list>'
        '<part id="P1"><measure number="1"><attributes><divisions>100</divisions></attributes>'
        + "".join(notas) + "</measure></part></score-partwise>"
    ).encode()


class PruebasPartituras(unittest.TestCase):
    def test_conversion_midi_transpuesta_tempo_y_bateria(self):
        original = extraer_partitura(midi(), ".mid").fragmentos[0]
        transportada = extraer_partitura(midi(tono=7, tempo=400000), ".mid").fragmentos[0]
        cambio = extraer_partitura(midi(cambiar=True), ".mid").fragmentos[0]
        self.assertEqual(original, [list(par) for par in zip(_NOTAS, _RITMO)])
        self.assertEqual([n + 7 for n, _ in original], [n for n, _ in transportada])
        self.assertEqual([d for _, d in original], [d for _, d in transportada])
        self.assertAlmostEqual(cambio[2][1] / cambio[0][1], 1.65, places=2)

    def test_musicxml_y_mxl_con_tempo_local(self):
        directo = extraer_partitura(musicxml(), ".musicxml")
        self.assertEqual(directo.fragmentos, extraer_partitura(midi(), ".mid").fragmentos)
        self.assertEqual(directo.compositor, "Autor de prueba")
        salida = BytesIO()
        with ZipFile(salida, "w") as comprimido:
            comprimido.writestr("META-INF/container.xml", '<container><rootfiles><rootfile full-path="score.xml"/>'
                                  '</rootfiles></container>')
            comprimido.writestr("score.xml", musicxml(cambiar=True))
        variado = extraer_partitura(salida.getvalue(), ".mxl")
        self.assertAlmostEqual(variado.fragmentos[0][5][1] / variado.fragmentos[0][0][1], 2, places=4)
        metronomo = (
            b"<direction-type><metronome><beat-unit>eighth</beat-unit><per-minute>120</per-minute>"
            b"</metronome></direction-type>"
        )
        self.assertEqual(extraer_partitura(musicxml(cambiar=True).replace(b'<sound tempo="60"/>', metronomo),
                                          ".musicxml"), variado)

    def test_valida_transcripciones_legibles_por_audio(self):
        estable = extraer_partitura(midi(), ".mid").fragmentos[0]
        inestable = [[nota, 0.15 if indice % 2 else 1] for indice, nota in enumerate(_NOTAS)]
        self.assertTrue(validar_reconocible(estable))
        self.assertFalse(validar_reconocible(inestable))

    def test_rechaza_corrupcion_entidades_y_pistas_ambiguas(self):
        for datos, extension in [
            (b"MThd\0\0", ".mid"), (b"PK datos", ".mxl"), (b"<score-partwise>", ".xml"),
            (b'<!DOCTYPE r [<!ENTITY x "texto">]><score-partwise>&x;</score-partwise>', ".xml"),
            (midi(ambiguo=True), ".mid"),
        ]:
            with self.subTest(extension=extension), self.assertRaises(ValueError):
                extraer_partitura(datos, extension)


class PruebasTelegramPartituras(unittest.IsolatedAsyncioTestCase):
    async def test_muestra_compositor_sin_ficha_de_grabacion(self):
        mensaje = Mock(reply_text=AsyncMock())
        with patch.object(bot, "_ficha_artista_cancion") as ficha:
            await bot._mostrar_cancion(mensaje, {
                "cancion": "Frase original", "artista": "Autor de prueba",
                "rol_artista": "compositor", "evidencia": "melodia",
            }, "alta", True)
        self.assertIn("Composición: Autor de prueba", mensaje.reply_text.call_args.args[0])
        ficha.assert_not_called()


class PruebasSincronizacion(unittest.TestCase):
    def setUp(self):
        self.base = Path(self.enterContext(TemporaryDirectory()))
        self.ruta = self.base / "melodias.json"
        self.ruta.write_text("[]", encoding="utf-8")
        self.config = self.base / "config.json"
        self.indice = self.base / "obras.json"
        self.config.write_text(json.dumps({
            "fuentes": ["manifiesto"], "manifiesto": self.indice.name,
            "max_archivos": 4, "intervalo_horas": 24, "max_bytes_catalogo": 100000,
            "prioridades": ["metal", "rock"],
        }), encoding="utf-8")
        self.cfg = configuracion(self.config)
        self.enterContext(redirect_stdout(StringIO()))

    def entrada(self, identificador, datos, extension=".mid", licencia=CC0):
        archivo = self.base / (identificador + extension)
        archivo.write_bytes(datos)
        return {
            "id": identificador, "archivo": archivo.name, "sha256": hashlib.sha256(datos).hexdigest(),
            "titulo": "Frase original", "compositor": "Autor de prueba",
            "url_original": f"https://example.org/{archivo.name}", "evidencia": "https://example.org/permiso",
            "atribucion": "Autor de prueba", "licencia_url": licencia, "composicion_y_edicion_autorizadas": True,
            "categoria": "rock" if extension == ".mid" else "metal",
        }

    def guardar_indice(self, *entradas):
        self.indice.write_text(json.dumps(entradas), encoding="utf-8")

    def test_simulacion_licencias_duplicados_prioridad_y_repeticion(self):
        self.guardar_indice(self.entrada("midi", midi()), self.entrada("xml", musicxml(), ".musicxml"),
                            self.entrada("incompatible", midi(), licencia="https://example.org/licencia-NC"))
        self.assertEqual(sincronizar(self.ruta, self.cfg, simular=True), 0)
        self.assertEqual(self.ruta.read_bytes(), b"[]")
        self.assertFalse(ruta_registro(self.ruta).exists())
        self.assertEqual(sincronizar(self.ruta, self.cfg), 0)
        contenido = self.ruta.read_bytes()
        obra, = json.loads(contenido)
        self.assertEqual(obra["id_origen"], "manifiesto/xml")
        self.assertEqual(obra["licencia"], "CC0-1.0")
        self.assertEqual(obra["rol_artista"], "compositor")
        self.assertTrue(obra["incorporado_en"])
        self.assertEqual([e["estado"] for e in cargar_registro(self.ruta)["eventos"]],
                         ["rechazado", "incorporado", "duplicado"])
        with patch("sincronizador_catalogo.descargar", side_effect=AssertionError):
            self.assertEqual(sincronizar(self.ruta, self.cfg), 0)
        self.assertEqual(self.ruta.read_bytes(), contenido)
        with patch("sincronizador_catalogo.ejecutar", side_effect=AssertionError):
            self.assertEqual(sincronizar(self.ruta, self.cfg, si_pendiente=True), 0)

    def test_red_y_fuente_inaccesible_reintentables(self):
        sesion = Mock()
        sesion.get.side_effect = requests.Timeout("https://example.org/secreto")
        with patch("fuentes_melodicas.sleep"):
            with self.assertRaisesRegex(ErrorFuente, "tres intentos"):
                Descargas(sesion).obtener("https://example.org/file.mid", 100, {"example.org"})
        self.assertEqual(sesion.get.call_count, 3)
        self.cfg["fuentes"] = ["openscore"]
        with patch("sincronizador_catalogo.openscore", side_effect=ErrorFuente("HTTP 503")):
            self.assertEqual(sincronizar(self.ruta, self.cfg), 1)
        estado = cargar_registro(self.ruta)
        self.assertIsNone(estado["ultimo_exito"])
        self.assertEqual(estado["eventos"][-1]["estado"], "error_fuente")
        self.assertEqual(self.ruta.read_bytes(), b"[]")

    def test_interrupcion_antes_de_reemplazar_y_reanudacion(self):
        self.guardar_indice(self.entrada("original", midi()))
        with patch.object(catalogo_melodias.os, "replace", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                sincronizar(self.ruta, self.cfg)
        self.assertEqual(self.ruta.read_bytes(), b"[]")
        self.assertFalse(list(self.base.glob("*.tmp")))
        self.assertFalse(ruta_registro(self.ruta).exists())
        self.assertEqual(sincronizar(self.ruta, self.cfg), 0)
        self.assertEqual(len(catalogo_melodias.leer_catalogo(self.ruta)), 1)

    def test_reconocimiento_recarga_y_credito_compositor(self):
        self.guardar_indice(self.entrada("original", midi()))
        with patch.dict(os.environ, {"MOSHPIT_CATALOGO_MELODIAS": str(self.ruta)}, clear=True), patch.object(
            audio, "consultar_gemini", return_value=(
                '{"cancion":null,"artista":null,"tipo":"desconocido","confianza":"baja"}', None
            )
        ) as gemini:
            wav = sintetizar("tarareo", tono=4, velocidad=0.8)
            self.assertIsNone(audio.analizar_audio(wav, "audio/wav")["cancion"])
            self.assertEqual(sincronizar(self.ruta, self.cfg), 0)
            for consulta in (wav, sintetizar("silbido", tono=-3, velocidad=1.15)):
                resultado = audio.analizar_audio(consulta, "audio/wav")
                self.assertEqual(resultado["evidencia"], "melodia")
                self.assertEqual(resultado["cancion"], "Frase original")
                self.assertIn("Composición: Autor de prueba", estilos.formatear_cancion(resultado, "alta", True))
            catalogo_melodias.guardar_catalogo(self.ruta, [])
            self.assertIsNone(audio.analizar_audio(wav, "audio/wav")["cancion"])
            self.assertEqual(gemini.call_count, 2)

    def test_indice_publicado_filtra_rutas_y_confirma_licencia(self):
        red = Mock()
        revision = "a" * 40
        red.obtener.side_effect = [
            json.dumps({"sha": revision, "commit": {"tree": {"sha": "b" * 40}}}).encode(),
            b"Creative Commons Legal Code\nCC0 1.0 Universal",
            b"1:\n  path: Autor\n  died: 1850\n2:\n  path: Reciente\n  died: 1990\n",
            json.dumps({"truncated": False, "tree": [
                {"path": "scores/Autor/_/Obra/lc123.mxl", "mode": "100644"},
                {"path": "scores/Autor/../Obra/lc9.mxl", "mode": "100644"},
                {"path": "scores/Reciente/_/Obra/lc555.mxl", "mode": "100644"},
                {"path": "scores/Autor/_/Enlace/lc666.mxl", "mode": "120000"},
            ]}).encode(),
        ]
        elemento, = openscore(red)
        self.assertEqual(elemento.clave, "openscore/123")
        self.assertIn(revision, elemento.url_original)
        self.assertEqual(elemento.licencia_url, CC0)
