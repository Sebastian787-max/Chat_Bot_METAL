import json
import sys
from io import BytesIO
import unittest
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock, patch
from urllib.parse import parse_qs, urlsplit

import httpx
from google.genai import errors
from PIL import Image
from telegram.error import TimedOut

import acordes
import estilos
import vision


_PARTITURA = {
    "cancion": "Título inventado por el modelo",
    "artista": "Texto libre inventado por el modelo",
    "tonalidad": "A menor",
    "afinacion": "Drop D",
    "secciones": [
        {"nombre": "intro", "acordes": ["Am", "G7", "D/F#"]},
        {"nombre": "coro", "acordes": ["Bbmaj7", "Cadd9"]},
    ],
    "confianza": "alta",
}


class PruebasAcordesTelegram(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        spec = spec_from_file_location("bot_acordes_simulado", Path(__file__).with_name("bot.py"))
        self.bot = module_from_spec(spec)
        with patch.dict(sys.modules, {"predict": SimpleNamespace(analizar_texto=Mock())}):
            spec.loader.exec_module(self.bot)
        self.mensaje = SimpleNamespace(reply_text=AsyncMock(), reply_photo=AsyncMock())
        self.update = SimpleNamespace(effective_message=self.mensaje)
        self.contexto = SimpleNamespace(args=["Mi", "banda", "-", "Mi", "tema"])
        self.enterContext(patch.object(vision, "_SATURACION", ((), 0.0)))

    def simular_gemini(self, datos):
        return patch.object(acordes, "consultar_gemini", return_value=(json.dumps(datos), None))

    async def test_comando_muestra_tono_afinacion_y_secciones_con_aviso(self):
        with self.simular_gemini(_PARTITURA):
            await self.bot.acordes(self.update, self.contexto)
        llamada = self.mensaje.reply_text.call_args
        self.assertEqual(llamada.args[0].splitlines(), [
            "🎸 Canción: Mi tema", "Artista: Mi banda", "Tono: A menor", "Afinación: Drop D",
            "", "Intro: Am · G7 · D/F#", "Coro: Bbmaj7 · Cadd9", "", estilos.AVISO_ACORDES,
            "", "Sin diagrama: Bbmaj7",
        ])
        self.assertTrue(llamada.kwargs["disable_web_page_preview"])
        botones = [b for fila in llamada.kwargs["reply_markup"].inline_keyboard for b in fila]
        self.assertEqual([b.text for b in botones], ["Ultimate Guitar", "Songsterr", "Cifra Club", "Ver letra"])
        self.assertEqual([parse_qs(urlsplit(b.url).query)["q"][0] for b in botones], [
            "site:ultimate-guitar.com Mi banda Mi tema chords", "site:songsterr.com Mi banda Mi tema",
            "site:cifraclub.com Mi banda Mi tema", "site:letras.com Mi banda Mi tema",
        ])
        self.mensaje.reply_photo.assert_awaited_once()
        envio = self.mensaje.reply_photo.call_args.kwargs
        self.assertEqual((envio["write_timeout"], envio["read_timeout"]), (40, 25))
        archivo = envio["photo"]
        self.assertEqual(archivo.filename, "acordes.png")
        with Image.open(BytesIO(archivo.input_file_content)) as png:
            self.assertEqual(png.format, "PNG")
            self.assertLessEqual(sum(png.size), 10_000)
            png.verify()
        self.assertLess(len(archivo.input_file_content), 10_000_000)
        self.assertIn("Posiciones en afinación estándar", self.mensaje.reply_photo.call_args.kwargs["caption"])
        self.assertIn("Ojo: la canción usa afinación Drop D", self.mensaje.reply_photo.call_args.kwargs["caption"])

    @staticmethod
    def timeout(causa):
        error = TimedOut()
        error.__cause__ = causa
        return error

    async def test_timeout_conexion_reintenta_sin_regenerar(self):
        self.mensaje.reply_photo.side_effect = [self.timeout(httpx.ConnectTimeout("conexión")), None]
        with self.simular_gemini(_PARTITURA) as gemini, patch.object(
            self.bot, "generar_diagramas", wraps=self.bot.generar_diagramas,
        ) as generar, patch.object(self.bot.asyncio, "sleep", new_callable=AsyncMock) as dormir:
            await self.bot.acordes(self.update, self.contexto)
        gemini.assert_called_once()
        generar.assert_called_once()
        dormir.assert_awaited_once_with(1)
        enviados = [c.kwargs["photo"].input_file_content for c in self.mensaje.reply_photo.await_args_list]
        self.assertEqual(len(enviados), 2)
        self.assertEqual(enviados[0], enviados[1])
        self.mensaje.reply_text.assert_awaited_once()

    async def test_timeout_repetido_avisa_y_conserva_texto(self):
        self.mensaje.reply_photo.side_effect = self.timeout(httpx.PoolTimeout("pool"))
        with self.simular_gemini(_PARTITURA), patch.object(
            self.bot.asyncio, "sleep", new_callable=AsyncMock,
        ), self.assertLogs(self.bot.__name__, level="ERROR"):
            await self.bot.acordes(self.update, self.contexto)
        self.assertEqual(self.mensaje.reply_photo.await_count, 2)
        self.assertEqual(self.mensaje.reply_text.await_count, 2)
        self.assertEqual(self.mensaje.reply_text.await_args.args, (estilos.DIAGRAMA_NO_ENVIADO,))

    async def test_timeout_ambiguo_no_duplica_la_foto(self):
        for causa in (httpx.ReadTimeout("lectura"), httpx.WriteTimeout("escritura"), None):
            with self.subTest(causa=causa):
                self.mensaje.reply_photo.reset_mock()
                self.mensaje.reply_photo.side_effect = self.timeout(causa)
                with self.simular_gemini(_PARTITURA), self.assertLogs(self.bot.__name__, level="ERROR"):
                    await self.bot.acordes(self.update, self.contexto)
                self.mensaje.reply_photo.assert_awaited_once()
                self.assertEqual(self.mensaje.reply_text.await_args.args, (estilos.DIAGRAMA_SIN_CONFIRMACION,))

    async def test_png_invalido_no_se_sube(self):
        with self.assertLogs(self.bot.__name__, level="ERROR"):
            aviso = await self.bot._enviar_diagrama_acordes(self.mensaje, b"archivo roto", "")
        self.assertEqual(aviso, estilos.DIAGRAMA_NO_ENVIADO)
        self.mensaje.reply_photo.assert_not_awaited()

    async def test_letra_y_texto_libre_se_eliminan_de_todos_los_campos(self):
        texto = "Una frase inventada entre los acordes"
        datos = dict(_PARTITURA, tonalidad=texto, afinacion=texto, secciones=[
            {"nombre": "intro", "acordes": ["Am", texto, "D/F#", "C\n" + texto, None, "Asus4"]},
            {"nombre": texto, "acordes": ["E"]},
            {"nombre": "puente", "acordes": [texto]},
        ])
        with self.simular_gemini(datos):
            await self.bot.acordes(self.update, self.contexto)
        self.assertEqual(self.mensaje.reply_text.call_args.args[0].splitlines(), [
            "🎸 Canción: Mi tema", "Artista: Mi banda", "Tono: Sin identificar",
            "Afinación: Sin identificar", "", "Intro: Am · D/F# · Asus4", "", estilos.AVISO_ACORDES,
        ])

    async def test_confianza_baja_o_sin_secciones_muestra_solo_mensaje_y_botones(self):
        for datos in (dict(_PARTITURA, confianza="baja"), dict(_PARTITURA, secciones=[])):
            with self.subTest(datos=datos), self.simular_gemini(datos):
                await self.bot.acordes(self.update, self.contexto)
            llamada = self.mensaje.reply_text.call_args
            self.assertEqual(llamada.args, (estilos.SIN_ACORDES,))
            self.assertEqual(sum(map(len, llamada.kwargs["reply_markup"].inline_keyboard)), 4)
        self.mensaje.reply_photo.assert_not_called()

    async def test_acorde_no_dibujable_aparece_en_texto_sin_enviar_una_imagen_vacia(self):
        datos = dict(_PARTITURA, secciones=[{"nombre": "intro", "acordes": ["C13b9"]}])
        with self.simular_gemini(datos):
            await self.bot.acordes(self.update, self.contexto)
        self.assertIn("Sin diagrama: C13b9", self.mensaje.reply_text.call_args.args[0])
        self.mensaje.reply_photo.assert_not_called()

    async def test_solo_titulo_y_boton_de_cancion_usan_el_mismo_flujo(self):
        pista = {"cancion": "Mi tema", "artista": "Mi banda", "portada": "https://example.test/a.jpg"}
        with patch.object(self.bot, "buscar_cancion", return_value=pista), patch.object(
            self.bot, "_ficha_artista_cancion", return_value="Ficha"
        ), self.simular_gemini(_PARTITURA) as gemini:
            await self.bot.acordes(self.update, SimpleNamespace(args=["Mi", "tema"]))
            await self.bot._mostrar_cancion(self.mensaje, pista, "alta", True)
            boton = self.mensaje.reply_photo.call_args.kwargs["reply_markup"].inline_keyboard[0][0]
            self.assertIn("Ver acordes", boton.text)
            self.assertLessEqual(len(boton.callback_data.encode()), 64)
            callback = SimpleNamespace(data=boton.callback_data, answer=AsyncMock(), message=self.mensaje)
            await self.bot.acordes_boton(SimpleNamespace(callback_query=callback), None)
        callback.answer.assert_awaited_once()
        self.assertEqual(gemini.call_args_list[0], gemini.call_args_list[1])
        self.assertEqual(self.mensaje.reply_photo.await_count, 3)
        self.assertIn("Posiciones en afinación estándar", self.mensaje.reply_photo.call_args.kwargs["caption"])

    async def test_429_y_503_agotan_reintentos_y_muestran_error_rockero(self):
        entorno = {"GEMINI_API_KEY": "ficticia", "GEMINI_MODEL": "principal",
                   "GEMINI_FALLBACK_MODEL": "respaldo"}
        for codigo in (429, 503):
            cliente = MagicMock()
            cliente.__enter__.return_value = cliente
            generar = cliente.models.generate_content
            generar.side_effect = errors.APIError(codigo, {"error": {"message": "Fallo simulado"}})
            with self.subTest(codigo=codigo), patch.dict("os.environ", entorno), patch.object(
                vision.genai, "Client", return_value=cliente
            ), patch.object(vision, "_registrar_solicitud"), patch.object(
                vision, "sleep"
            ) as dormir, self.assertLogs(vision.__name__, level="INFO"):
                await self.bot.acordes(self.update, self.contexto)
            self.assertEqual([c.kwargs["model"] for c in generar.call_args_list],
                             ["principal"] * 3 + ["respaldo"] * 3)
            self.assertEqual([llamada.args[0] for llamada in dormir.call_args_list], [2, 4, 2, 4])
            esperado = estilos.GEMINI_SATURADO if codigo == 503 else estilos.ERROR_ACORDES
            self.assertEqual(self.mensaje.reply_text.call_args.args, (esperado,))
