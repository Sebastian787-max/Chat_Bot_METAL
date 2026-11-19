import sys
import unittest
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import lastfm
from generos import detectar_genero


class PruebasEnrutamiento(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        # El enrutamiento se verifica sin cargar los modelos de pysentimiento.
        spec = spec_from_file_location("bot_simulado", Path(__file__).with_name("bot.py"))
        self.bot = module_from_spec(spec)
        self.analizar = Mock(return_value=("tristeza", "no", 0, "Bajo", "normal", 90))
        with patch.dict(sys.modules, {"predict": SimpleNamespace(analizar_texto=self.analizar)}):
            spec.loader.exec_module(self.bot)
        self.contexto = SimpleNamespace(user_data={}, args=[])
        self.artistas = [f"Grupo {numero:02}" for numero in range(30)]
        self.top = Mock(return_value=self.artistas)
        nombres = {"metallica": "Metallica", "nirvana": "Nirvana", "slayer": "Slayer"}
        self.buscar = Mock(side_effect=lambda texto: nombres.get(texto.casefold()))
        self.similares = Mock(return_value=["Anthrax"])
        self.ficha = Mock(side_effect=lambda artista: f"Ficha de {artista.capitalize()}")
        for nombre, doble in {
            "obtener_top_artistas_por_genero": self.top,
            "obtener_canciones_top": Mock(side_effect=lambda artista: [f"Tema de {artista}"]),
            "buscar_artista": self.buscar,
            "obtener_similares": self.similares,
            "_ficha_banda": self.ficha,
        }.items():
            self.enterContext(patch.object(self.bot, nombre, doble))

    async def enviar(self, texto, chat_id=10):
        mensaje = SimpleNamespace(
            text=texto, chat_id=chat_id, message_thread_id=None, reply_text=AsyncMock()
        )
        update = SimpleNamespace(effective_message=mensaje)
        await self.bot._cerrar_tanda_activa(update, self.contexto)
        handler = self.bot.responder
        if texto.startswith("/"):
            comando, *self.contexto.args = texto[1:].split()
            handler = getattr(self.bot, comando)
        await handler(update, self.contexto)
        return mensaje.reply_text.call_args.args[0]

    async def test_recomendaciones_y_redireccion_de_comandos(self):
        for texto, etiqueta in (
            ("algo de black metal", "black metal"),
            ("/banda quiero conocer algo de black metal, que me recomiendas?", "black metal"),
            ("recomiéndame thrash", "thrash metal"),
            ("quiero conocer death metal, qué me recomiendas?", "death metal"),
            ("/similares metal sinfónico", "symphonic metal"),
            ("/genero rock clásico", "classic rock"),
        ):
            with self.subTest(texto=texto):
                salida = await self.enviar(texto)
                self.top.assert_called_with(etiqueta)
                self.assertEqual(salida.count("Arranca con"), 5)
        self.buscar.assert_not_called()
        self.analizar.assert_not_called()

    async def test_artista_similares_y_emocion(self):
        self.assertIn("Anthrax", await self.enviar("algo parecido a Metallica"))
        self.similares.assert_called_once_with("Metallica")
        self.assertEqual(await self.enviar("Metallica"), "Ficha de Metallica")
        self.assertIn("Nirvana", await self.enviar("Estoy muy triste"))
        self.analizar.assert_called_once_with("Estoy muy triste")

    async def test_otras_sin_repetir_y_solo_en_la_conversacion_activa(self):
        primera = await self.enviar("algo de black metal")
        segunda = await self.enviar("otras")
        self.assertEqual(sum(artista in primera for artista in self.artistas), 5)
        self.assertEqual(sum(artista in segunda for artista in self.artistas), 5)
        self.assertFalse(any(artista in primera and artista in segunda for artista in self.artistas))
        self.assertEqual(await self.enviar("otras", chat_id=20), self.bot.estilos.PEDIR_GENERO)
        await self.enviar("Metallica")
        self.assertEqual(await self.enviar("otras"), self.bot.estilos.PEDIR_GENERO)

    async def test_pide_genero_y_lista_opciones(self):
        for texto in ("recomiéndame algo", "qué escucho?", "/banda recomienda algo"):
            self.assertEqual(await self.enviar(texto), self.bot.estilos.PEDIR_GENERO)
        self.assertIn("post-rock", await self.enviar("/generos"))
        self.analizar.assert_not_called()

    async def test_recomendar_con_y_sin_argumentos(self):
        self.assertIn("Dato curioso", await self.enviar("/recomendar"))
        self.assertEqual(await self.enviar("/recomendar quiero saber de nirvana"), "Ficha de Nirvana")
        self.buscar.assert_called_once_with("nirvana")
        self.assertEqual((await self.enviar("/recomendar black metal")).count("Arranca con"), 5)
        self.top.assert_called_once_with("black metal")
        for prefijo in ("algo parecido a", "parecido a", "similar a"):
            self.assertIn("Anthrax", await self.enviar(f"/recomendar {prefijo} Metallica"))
            self.similares.assert_called_with("Metallica")
        self.assertIn("/generos", await self.enviar("/recomendar xyzabc123"))

    async def test_limpia_consultas_de_banda(self):
        self.assertEqual(await self.enviar("quiero saber de nirvana"), "Ficha de Nirvana")
        self.assertEqual(await self.enviar("háblame de Slayer"), "Ficha de Slayer")
        self.assertEqual(await self.enviar("/banda cuentame de Slayer"), "Ficha de Slayer")
        self.analizar.assert_not_called()


class PruebasGenerosYLastFM(unittest.TestCase):
    def test_aliases_y_frases_largas(self):
        self.assertEqual(detectar_genero("Rock y sobre todo rock PROGRESÍVO"), "progressive rock")
        self.assertEqual(detectar_genero("metal negro"), "black metal")
        self.assertIsNone(detectar_genero("rockero"))

    def test_parametros_y_respuestas_simuladas(self):
        with patch.object(lastfm, "_consultar", side_effect=[
            {"topartists": {"artist": [{"name": "Grupo"}]}},
            {"toptracks": {"track": [{"name": "Tema"}]}},
        ]) as consultar:
            self.assertEqual(lastfm.obtener_top_artistas_por_genero("black metal"), ["Grupo"])
            self.assertEqual(lastfm.obtener_canciones_top("Grupo"), ["Tema"])
        self.assertEqual(consultar.call_args_list[0].kwargs, {"tag": "black metal", "limit": 30})
        self.assertEqual(consultar.call_args_list[0].args, ("tag.gettopartists",))
        self.assertEqual(consultar.call_args_list[1].args, ("artist.gettoptracks",))
