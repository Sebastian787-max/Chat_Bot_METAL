import sys
from struct import pack
import unittest
import json
import wave
from importlib.util import module_from_spec, spec_from_file_location
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import quote
from unittest.mock import AsyncMock, Mock, patch

import requests
from google.genai import errors, types

import audio
import letras
import portadas
import vision


_JSON_AUDIO = (
    '{"cancion":"Enter Sandman","artista":"Metallica","tipo":"grabacion",'
    '"letra_escuchada":null,"confianza":"alta"}'
)
_PISTA = {
    "cancion": "Enter Sandman", "artista": "Metallica", "album": "Metallica",
    "portada": "https://is1-ssl.mzstatic.com/arte/600x600.jpg",
}


def _wav(segundos):
    salida = BytesIO()
    with wave.open(salida, "wb") as archivo:
        archivo.setparams((1, 2, 8000, 0, "NONE", ""))
        ciclo = b"".join(
            pack("<h", 4000 if paso < 10 else -4000) for paso in range(20)
        )
        archivo.writeframes(ciclo * 400 * segundos)
    return salida.getvalue()


class PruebasAudio(unittest.TestCase):
    def test_gemini_correcto_con_json_en_bloque(self):
        respuesta = "```json\n" + _JSON_AUDIO + "\n```"
        with patch.object(audio, "consultar_gemini", return_value=(respuesta, None)) as gemini, patch.object(
            audio, "buscar_cancion", return_value=_PISTA
        ):
            datos = audio.analizar_audio(_wav(2), "audio/x-wav")
        self.assertIsNone(datos["cancion"])
        self.assertEqual(datos["alternativas"], [{"cancion": "Enter Sandman", "artista": "Metallica"}])
        self.assertEqual(gemini.call_args.args[0][1].inline_data.mime_type, "audio/wav")

    def test_json_invalido_vacio_y_bloqueo(self):
        bloqueada = types.GenerateContentResponse(prompt_feedback={"block_reason": "SAFETY"})
        for texto, respuesta in (("Texto en prosa", None), ("", None), (_JSON_AUDIO, bloqueada)):
            with self.subTest(texto=texto), patch.object(
                audio, "consultar_gemini", return_value=(texto, respuesta)
            ), self.assertLogs(audio.__name__, level="ERROR"):
                datos = audio.analizar_audio(_wav(2), "audio/wav")
                self.assertIsNone(datos["cancion"])
                self.assertEqual(datos["confianza"], "baja")

    def test_audio_vacio_y_largo_se_rechazan_antes_de_gemini(self):
        with patch.object(audio, "consultar_gemini") as gemini:
            for datos, motivo in ((b"", "audio_vacio"), (_wav(61), "audio_largo")):
                with self.subTest(motivo=motivo), self.assertRaisesRegex(vision.ErrorVision, motivo):
                    audio.analizar_audio(datos, "audio/wav")
        gemini.assert_not_called()

    def test_ogg_rechazado_se_reintenta_como_mp3(self):
        cliente = Mock()
        modelo = cliente.models.generate_content
        modelo.side_effect = [
            errors.APIError(400, {"error": {"message": "Unsupported audio format"}}),
            SimpleNamespace(text=_JSON_AUDIO),
        ]

        def consultar(contenido, esquema, *, corregir_formato, limite_total):
            respuesta = vision._generar_con_reintentos(
                cliente, ("modelo-simulado",), contenido, esquema, corregir_formato,
                limite_total=limite_total,
            )
            return respuesta.text, None

        with patch.object(audio, "_validar_audio"), patch.object(
            audio, "preparar_audio", return_value=(None, False)
        ), patch.object(audio, "buscar_cancion", return_value=_PISTA), patch.object(
            audio, "_convertir_a_mp3", return_value=b"mp3"
        ), patch.object(audio, "consultar_gemini", side_effect=consultar), self.assertLogs(
            vision.__name__, level="INFO"
        ):
            resultado = audio.analizar_audio(b"ogg", "audio/ogg")
            self.assertIsNone(resultado["cancion"])
            self.assertEqual(resultado["alternativas"][0]["cancion"], "Enter Sandman")
        self.assertEqual(
            [llamada.kwargs["contents"][1].inline_data.mime_type for llamada in modelo.call_args_list],
            ["audio/ogg", "audio/mpeg"],
        )


class PruebasPortadas(unittest.TestCase):
    def test_itunes_prefiere_titulo_y_artista_coincidentes(self):
        respuesta = Mock(status_code=200)
        respuesta.json.return_value = {"results": [
            {"trackName": "Enter Sandman", "artistName": "Banda tributo"},
            {"trackName": "Enter Sandman", "artistName": "Metallica", "collectionName": "Metallica",
             "artworkUrl100": "https://is1-ssl.mzstatic.com/arte/100x100.jpg"},
        ]}
        with patch.object(portadas.requests, "get", return_value=respuesta) as obtener:
            self.assertEqual(portadas.buscar_cancion("enter sandmán", "metállica"), _PISTA)
        self.assertEqual(obtener.call_args.kwargs["params"]["entity"], "song")

    def test_busqueda_directa_no_acepta_un_titulo_meramente_parecido(self):
        respuesta = Mock(status_code=200)
        respuesta.json.return_value = {"results": [
            {"trackName": "Forest", "artistName": "Otra banda"},
        ]}
        with patch.object(portadas.requests, "get", return_value=respuesta), patch.object(
            portadas, "buscar_pistas", return_value=[]
        ):
            self.assertIsNone(portadas.buscar_cancion("Forests", titulo_exacto=True))

    def test_itunes_vacio_y_lastfm_sin_coincidencia(self):
        respuesta = Mock(status_code=200)
        respuesta.json.return_value = {"results": []}
        with patch.object(portadas.requests, "get", return_value=respuesta), patch.object(
            portadas, "buscar_pistas", return_value=[]
        ):
            self.assertIsNone(portadas.buscar_cancion("Enter Sandman", "Metallica"))

    def test_itunes_caido_usa_lastfm_y_descarta_placeholder(self):
        datos = [{"name": "Enter Sandman", "artist": "Metallica"}]
        info = {"album": {"title": "Metallica", "image": [
            {"#text": ""},
            {"#text": "https://lastfm.freetls.fastly.net/i/u/300x300/2a96cbd8b46e442fc41c2b86b821562f.jpg"},
        ]}}
        with patch.object(portadas.requests, "get", side_effect=requests.ConnectionError), patch.object(
            portadas, "buscar_pistas", return_value=datos
        ), patch.object(portadas, "obtener_info_pista", return_value=info):
            self.assertEqual(portadas.buscar_cancion("Enter Sandman", "Metallica"),
                             dict(_PISTA, portada=None))


class PruebasAudioTelegram(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        spec = spec_from_file_location("bot_audio_simulado", Path(__file__).with_name("bot.py"))
        self.bot = module_from_spec(spec)
        with patch.dict(sys.modules, {"predict": SimpleNamespace(analizar_texto=Mock())}):
            spec.loader.exec_module(self.bot)
        self.mensaje = SimpleNamespace(
            reply_text=AsyncMock(), reply_photo=AsyncMock(), reply_chat_action=AsyncMock(),
        )
        self.update = SimpleNamespace(effective_message=self.mensaje)
        self.enterContext(patch.object(self.bot, "_ficha_artista_cancion", return_value="Similares"))
        self.enterContext(patch.object(audio, "verificar_letra", return_value="sin_datos"))

    async def test_fragmento_escrito_con_portada_sin_repetir_la_letra(self):
        self.mensaje.text = "¿Qué canción dice «fragmento de prueba»?"
        datos = {"cancion": "Enter Sandman", "artista": "Metallica", "confianza": "media"}
        with patch.object(audio, "consultar_gemini", return_value=(
            '{"cancion":"Enter Sandman","artista":"Metallica","confianza":"media"}', None
        )) as gemini, patch.object(self.bot, "buscar_cancion", return_value=_PISTA) as buscar:
            await self.bot.responder(self.update, None)
        self.assertIn("fragmento de prueba", gemini.call_args.args[0][0])
        buscar.assert_called_once_with(datos["cancion"], datos["artista"], titulo_exacto=True)
        llamada = self.mensaje.reply_photo.call_args.kwargs
        self.assertEqual(llamada["photo"], _PISTA["portada"])
        self.assertIn("creo que es", llamada["caption"])
        self.assertIn("sin verificar la letra", llamada["caption"])
        self.assertIn("aparecen en el catálogo", llamada["caption"])
        self.assertNotIn("fragmento de prueba", llamada["caption"])
        self.mensaje.reply_text.assert_awaited_once_with("Similares")

    async def test_nota_de_voz_sin_evidencia_no_se_confirma(self):
        archivo = SimpleNamespace(download_as_bytearray=AsyncMock(return_value=bytearray(b"ogg")))
        self.mensaje.voice = SimpleNamespace(duration=15, file_size=3, get_file=AsyncMock(return_value=archivo))
        datos = {"cancion": "Enter Sandman", "artista": "Metallica", "confianza": "alta"}
        with patch.object(self.bot, "analizar_audio", return_value=datos) as analizar, patch.object(
            self.bot, "buscar_cancion", return_value=None
        ):
            await self.bot.audio_recibido(self.update, None)
        analizar.assert_called_once_with(b"ogg", "audio/ogg")
        self.assertIn("Analizando tu audio", self.mensaje.reply_text.call_args_list[0].args[0])
        self.assertIn("No pude identificar", self.mensaje.reply_text.call_args_list[1].args[0])
        self.mensaje.reply_photo.assert_not_called()

    async def test_nota_confirmada_muestra_portada_genero_escucha_y_acordes(self):
        archivo = SimpleNamespace(download_as_bytearray=AsyncMock(return_value=bytearray(b"ogg")))
        self.mensaje.voice = SimpleNamespace(duration=15, file_size=3, get_file=AsyncMock(return_value=archivo))
        datos = {"cancion": "Enter Sandman", "artista": "Metallica", "confianza": "alta", "evidencia": "huella"}
        pista = dict(_PISTA, genero="Metal", escuchar="https://music.apple.com/us/song/1")
        with patch.object(self.bot, "analizar_audio", return_value=datos), patch.object(
            self.bot, "buscar_cancion", return_value=pista
        ) as buscar:
            await self.bot.audio_recibido(self.update, None)
        buscar.assert_called_once_with("Enter Sandman", "Metallica", titulo_exacto=True)
        envio = self.mensaje.reply_photo.call_args.kwargs
        self.assertEqual(envio["photo"], _PISTA["portada"])
        for frase in ("Canción identificada", "Enter Sandman", "Metallica", "Género musical: Metal"):
            self.assertIn(frase, envio["caption"])
        botones = envio["reply_markup"].inline_keyboard
        self.assertTrue(botones[0][0].callback_data.startswith("acordes:"))
        self.assertEqual(botones[1][0].url, pista["escuchar"])

    async def test_comando_titulo_busca_directo(self):
        contexto = SimpleNamespace(args=["Enter", "Sandman"])
        with patch.object(self.bot, "buscar_cancion", return_value=_PISTA) as buscar, patch.object(
            self.bot, "identificar_por_letra"
        ) as gemini:
            await self.bot.cancion(self.update, contexto)
        buscar.assert_called_once_with("Enter Sandman", titulo_exacto=True)
        gemini.assert_not_called()
        self.mensaje.reply_photo.assert_awaited_once()

    async def test_confianza_baja_muestra_alternativas_y_enlaces_sin_vista_previa(self):
        fragmento = 'luces de cartón & acero / azul'
        self.mensaje.text = f"qué canción dice {fragmento}?"
        candidatos = [{"cancion": f"Tema {i}", "artista": f"Banda {i}"} for i in range(3)]
        texto = json.dumps({"confianza": "baja", "alternativas": candidatos})
        with patch.object(audio, "consultar_gemini", return_value=(texto, None)), patch.object(
            self.bot, "buscar_cancion"
        ) as catalogo:
            await self.bot.responder(self.update, None)
        catalogo.assert_not_called()
        self.mensaje.reply_photo.assert_not_called()
        envio = self.mensaje.reply_text.call_args
        respuesta = envio.args[0]
        self.assertIn("no estoy seguro", respuesta)
        self.assertIn("Podría ser", respuesta)
        self.assertIn("Tema 0", respuesta)
        self.assertIn("Tema 1", respuesta)
        self.assertNotIn("Tema 2", respuesta)
        self.assertNotIn(fragmento, respuesta)
        self.assertTrue(envio.kwargs["disable_web_page_preview"])
        botones = envio.kwargs["reply_markup"].inline_keyboard[0]
        self.assertEqual([boton.url for boton in botones], [
            "https://www.google.com/search?q=" + quote(f'"{fragmento}" lyrics', safe=""),
            "https://genius.com/search?q=" + quote(fragmento, safe=""),
            "https://bandcamp.com/search?q=" + quote(fragmento, safe=""),
        ])

    async def test_forest_repetido_y_letra_distinta_no_muestra_portada(self):
        fragmento = (
            "Forest bajo cables de cobre\nForest vuela sobre faros partidos\n"
            "Forest gira alrededor del reloj\nHumo dibuja círculos sobre piedra\n"
            "Forest resuena detrás del vidrio\nCampanas vibran sobre puentes\nForest entre chispas de estaño"
        )
        self.mensaje.text = f"qué canción dice {fragmento}?"
        candidato = json.dumps({"cancion": "Forest", "artista": "System of a Down", "confianza": "media"})
        letra_privada = "Nubes lejanas bailan juntas\nLas montañas se hunden despacio"
        api = Mock()
        api.json.return_value = {"plainLyrics": letra_privada}
        with patch.object(audio, "consultar_gemini", return_value=(candidato, None)), patch.object(
            audio, "verificar_letra", wraps=letras.verificar_letra
        ), patch.object(letras.requests, "get", return_value=api), patch.object(
            self.bot, "buscar_cancion"
        ) as catalogo, self.assertLogs(letras.__name__, level="INFO") as registro:
            await self.bot.responder(self.update, None)
        catalogo.assert_not_called()
        self.mensaje.reply_photo.assert_not_called()
        envio = self.mensaje.reply_text.call_args
        self.assertIn("no estoy seguro", envio.args[0])
        self.assertNotIn("creo que es", envio.args[0])
        self.assertNotIn("System of a Down", envio.args[0])
        self.assertNotIn(letra_privada, envio.args[0])
        self.assertNotIn(fragmento, envio.args[0])
        self.assertEqual(len(envio.kwargs["reply_markup"].inline_keyboard[0]), 3)
        self.assertNotIn("Nubes lejanas", "\n".join(registro.output))

    async def test_alternativa_verificada_muestra_confirmacion_sin_letra(self):
        fragmento = "Luces de cartón tiemblan sobre puentes oxidados"
        self.mensaje.text = f"busca esta letra: {fragmento}"
        candidato = json.dumps({
            "cancion": "Otra pista", "artista": "Otro artista", "confianza": "alta",
            "alternativas": [{"cancion": "Enter Sandman", "artista": "Metallica"}],
        })
        respuestas = [Mock(), Mock()]
        respuestas[0].json.return_value = {"plainLyrics": "Vientos del norte visitan la costa"}
        respuestas[1].json.return_value = {"plainLyrics": fragmento + "\nEstrofa privada adicional"}
        with patch.object(audio, "consultar_gemini", return_value=(candidato, None)), patch.object(
            audio, "verificar_letra", wraps=letras.verificar_letra
        ), patch.object(letras.requests, "get", side_effect=respuestas), patch.object(
            self.bot, "buscar_cancion", return_value=_PISTA
        ) as catalogo, self.assertLogs(letras.__name__, level="INFO") as registro:
            await self.bot.responder(self.update, None)
        catalogo.assert_called_once_with("Enter Sandman", "Metallica", titulo_exacto=True)
        pie = self.mensaje.reply_photo.call_args.kwargs["caption"]
        self.assertIn("La letra coincide", pie)
        self.assertNotIn(fragmento, pie)
        self.assertNotIn("Estrofa privada", pie)
        self.assertNotIn("Estrofa privada", "\n".join(registro.output))


class PruebasLetraConBusqueda(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.dict("os.environ", {"GEMINI_BUSQUEDA_WEB": "si"}))
        self.enterContext(patch.object(audio, "_BUSQUEDA_SUSPENDIDA_HASTA", 0))
        self.verificar = self.enterContext(patch.object(audio, "verificar_letra", return_value="sin_datos"))
        self.texto = json.dumps({
            "cancion": "Cielo de latón", "artista": "La Banda de Prueba", "confianza": "alta",
            "evidencia": "Un resultado de búsqueda atribuye el fragmento a ese tema.",
            "alternativas": [],
        })
        self.respuesta = types.GenerateContentResponse(candidates=[
            types.Candidate(grounding_metadata=types.GroundingMetadata(
                web_search_queries=["fragmento inventado lyrics"]
            ))
        ])

    def test_busqueda_con_letra_confirmada_admite_confianza_alta(self):
        self.verificar.return_value = "coincide"
        with patch.object(audio, "consultar_gemini", return_value=(self.texto, self.respuesta)) as gemini:
            resultado = audio.identificar_por_letra("bajo un cielo de latón")
        self.assertEqual(resultado["confianza"], "alta")
        self.assertTrue(resultado["letra_verificada"])
        self.assertIsNotNone(gemini.call_args.kwargs["herramientas"][0].google_search)

    def test_alta_sin_grounding_se_reduce_a_media(self):
        with patch.object(audio, "consultar_gemini", return_value=(self.texto, None)):
            resultado = audio.identificar_por_letra("las campanas derraman ecos sobre puentes")
        self.assertEqual(resultado["confianza"], "media")
        self.assertFalse(resultado["letra_verificada"])

    def test_palabra_repetida_no_basta_para_identificar(self):
        texto = json.dumps({"cancion": "Forest", "artista": "Otra banda", "confianza": "alta"})
        with patch.object(audio, "consultar_gemini", return_value=(texto, None)):
            resultado = audio.identificar_por_letra("Forest, forest, Forest")
        self.assertEqual(resultado["confianza"], "baja")
        self.assertIsNone(resultado["cancion"])

    def test_opcion_desactivada_no_usa_busqueda_incluso_con_metadatos(self):
        with patch.dict("os.environ", {"GEMINI_BUSQUEDA_WEB": "no"}), patch.object(
            audio, "consultar_gemini", return_value=(self.texto, self.respuesta)
        ) as gemini:
            resultado = audio.identificar_por_letra("las campanas derraman ecos sobre puentes")
        self.assertEqual(resultado["confianza"], "media")
        self.assertFalse(resultado["letra_verificada"])
        self.assertNotIn("herramientas", gemini.call_args.kwargs)

    def test_herramienta_no_soportada_y_cuota_vuelven_a_consultar_sin_busqueda(self):
        for codigo, mensaje, intentos in ((400, "Google Search tool not supported", 2),
                                          (429, "Quota exceeded", 1)):
            with self.subTest(codigo=codigo):
                cliente = Mock()
                generar = cliente.models.generate_content

                def responder(*, model, contents, config):
                    if config.tools:
                        self.assertIsNone(config.response_mime_type)
                        self.assertIsNone(config.response_schema)
                        raise errors.APIError(codigo, {"error": {"message": mensaje}})
                    return self.respuesta

                def consultar(contenido, esquema, **opciones):
                    respuesta = vision._generar_con_reintentos(
                        cliente, ("principal", "respaldo"), contenido, esquema,
                        herramientas=opciones.get("herramientas"),
                        limite_total=opciones["limite_total"],
                    )
                    return self.texto, respuesta

                generar.side_effect = responder
                with patch.object(audio, "consultar_gemini", side_effect=consultar) as gemini, patch.object(
                    vision, "sleep"
                ), self.assertLogs(vision.__name__, level="INFO"):
                    resultado = audio.identificar_por_letra("las campanas derraman ecos sobre puentes")
                self.assertEqual(generar.call_count, intentos + 1)
                self.assertFalse(gemini.call_args.kwargs["registrar_solicitud"])
                self.assertEqual(resultado["confianza"], "media")
                self.assertFalse(resultado["letra_verificada"])

    def test_cuota_suspende_google_search_hasta_que_venzan_los_30_minutos(self):
        reloj = Mock(return_value=100)
        fragmento = "cables de papel en la niebla"
        with patch.object(audio, "monotonic", side_effect=reloj), patch.object(
            audio, "consultar_gemini", side_effect=[
                vision.ErrorVision("cuota_busqueda"),
                (self.texto, self.respuesta),
                (self.texto, self.respuesta),
                (self.texto, self.respuesta),
            ]
        ) as gemini, self.assertLogs(audio.__name__, level="INFO") as registro:
            primera = audio.identificar_por_letra(fragmento)
            reloj.return_value = 1899
            segunda = audio.identificar_por_letra(fragmento)
            reloj.return_value = 1900
            tercera = audio.identificar_por_letra(fragmento)

        self.assertEqual([bool(c.kwargs.get("herramientas")) for c in gemini.call_args_list],
                         [True, False, False, True])
        for resultado in (primera, segunda):
            self.assertEqual(resultado["confianza"], "media")
            self.assertFalse(resultado["letra_verificada"])
        self.assertFalse(tercera["letra_verificada"])
        self.assertEqual([r.getMessage() for r in registro.records],
                         ["Google Search sin cuota; se desactiva por 30 min"])

    def test_503_con_busqueda_mantiene_reintentos_y_respaldo(self):
        cliente = Mock()
        generar = cliente.models.generate_content
        error = errors.APIError(503, {"error": {"message": "High demand"}})
        generar.side_effect = [error, error, error, self.respuesta]
        herramienta = types.Tool(google_search=types.GoogleSearch())
        with patch.object(vision, "sleep") as dormir, self.assertLogs(
            vision.__name__, level="INFO"
        ) as registro:
            resultado = vision._generar_con_reintentos(
                cliente, ("principal", "respaldo"), [], herramientas=[herramienta],
            )
        self.assertIs(resultado, self.respuesta)
        self.assertEqual([c.kwargs["model"] for c in generar.call_args_list],
                         ["principal"] * 3 + ["respaldo"])
        self.assertEqual([c.args[0] for c in dormir.call_args_list], [2, 4])
        self.assertTrue(all(c.kwargs["config"].tools for c in generar.call_args_list))
        self.assertFalse(any("Traceback" in linea for linea in registro.output))

    def test_audio_con_fragmento_y_confianza_media_busca_en_web(self):
        self.verificar.return_value = "coincide"
        texto_audio = json.dumps({
            "cancion": "Pista tentativa", "artista": "Banda tentativa", "confianza": "media",
            "tipo": "cantado", "letra_escuchada": "cielo de latón bajo estrellas",
        })
        with patch.object(audio, "consultar_gemini", side_effect=[
            (texto_audio, None), (self.texto, self.respuesta),
        ]) as gemini, patch.object(audio, "buscar_cancion", return_value={
            "cancion": "Cielo de latón", "artista": "Banda del catálogo",
        }):
            resultado = audio.analizar_audio(_wav(2), "audio/wav")
        self.assertEqual(resultado["cancion"], "Cielo de latón")
        self.assertTrue(resultado["letra_verificada"])
        self.assertIn("herramientas", gemini.call_args.kwargs)

    def test_audio_con_confianza_alta_tambien_verifica_la_letra(self):
        texto = json.dumps({"cancion": "Forest", "artista": "Otra banda", "confianza": "alta",
                            "letra_escuchada": "Forest susurra entre cables", "tipo": "cantado"})
        self.verificar.return_value = "no_coincide"
        with patch.object(audio, "consultar_gemini", return_value=(texto, None)):
            resultado = audio.analizar_audio(_wav(2), "audio/wav")
        self.verificar.assert_called_once_with("Otra banda", "Forest", "Forest susurra entre cables")
        self.assertEqual(resultado["confianza"], "baja")
        self.assertIsNone(resultado["cancion"])

    def test_servicios_caidos_solo_dejan_media_si_el_titulo_no_esta_en_el_fragmento(self):
        with patch.object(audio, "consultar_gemini", return_value=(self.texto, self.respuesta)), patch.object(
            audio, "verificar_letra", wraps=letras.verificar_letra
        ), patch.object(letras.requests, "get", side_effect=requests.ConnectionError) as pedir, self.assertLogs(
            letras.__name__, level="INFO"
        ) as registro:
            trampa = audio.identificar_por_letra("Cielo de latón entre engranajes de papel")
            tentativa = audio.identificar_por_letra("Las campanas derraman ecos sobre puentes")
        self.assertEqual(trampa["confianza"], "baja")
        self.assertIsNone(trampa["cancion"])
        self.assertEqual(tentativa["confianza"], "media")
        self.assertFalse(tentativa["letra_verificada"])
        self.assertTrue(all("sin_datos" in linea for linea in registro.output))
        self.assertTrue(any("lyrics.ovh" in linea for linea in registro.output))
        self.assertTrue(all(0 < c.kwargs["timeout"] <= 8 for c in pedir.call_args_list))

    def test_si_tambien_agota_cuota_sin_busqueda_devuelve_resultado_incierto(self):
        fragmento = "cables de papel en la niebla"
        with patch.object(audio, "consultar_gemini", side_effect=[
            vision.ErrorVision("busqueda_no_disponible"), vision.ErrorVision("cuota"),
        ]):
            resultado = audio.identificar_por_letra(fragmento)
        self.assertEqual(resultado["fragmento"], fragmento)
        self.assertEqual(resultado["confianza"], "baja")
        self.assertIsNone(resultado["cancion"])


class PruebasServiciosLetra(unittest.TestCase):
    def test_busqueda_lrclib_exige_artista_y_titulo_antes_de_comparar(self):
        fragmento = "Brújulas de acero rondan una estación"
        obtener, buscar = Mock(), Mock()
        obtener.json.return_value = {}
        buscar.json.return_value = [
            {"artistName": "Otra banda", "trackName": "Tema", "plainLyrics": "Texto privado distinto"},
            {"artistName": "Mi banda", "trackName": "Tema", "plainLyrics": fragmento},
        ]
        with patch.object(letras.requests, "get", side_effect=[obtener, buscar]) as pedir, self.assertLogs(
            letras.__name__, level="INFO"
        ) as registro:
            estado = letras.verificar_letra("Mi banda", "Tema", fragmento)
        self.assertEqual(estado, "coincide")
        self.assertEqual([c.args[0].rsplit("/", 1)[-1] for c in pedir.call_args_list], ["get", "search"])
        self.assertNotIn(fragmento, "\n".join(registro.output))

    def test_lyrics_ovh_codifica_datos_y_compara_dos_lineas_similares(self):
        fragmento = ("Puentes dorados guardan antiguos secretos\n"
                     "Faros partidos iluminan oscuros acantilados\n"
                     "Girasoles prismáticos vibran giran señalan campanas ceniza nácar ámbar")
        letra = ("Puentes dorados guardan antiguos recuerdos\n"
                 "Faros partidos iluminan oscuros océanos")
        vacia, disponible = Mock(), Mock()
        vacia.json.return_value = {}
        disponible.json.return_value = {"lyrics": letra}
        with patch.object(letras.requests, "get", side_effect=[vacia, vacia, disponible]) as pedir, self.assertLogs(
            letras.__name__, level="INFO"
        ) as registro:
            estado = letras.verificar_letra("Banda / Ensayo", "Canción & Noche", fragmento)
        self.assertEqual(estado, "coincide")
        self.assertTrue(pedir.call_args.args[0].endswith("Banda%20%2F%20Ensayo/Canci%C3%B3n%20%26%20Noche"))
        self.assertNotIn("Puentes dorados", "\n".join(registro.output))

