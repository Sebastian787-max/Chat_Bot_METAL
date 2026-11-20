import unittest
from unittest.mock import Mock, patch

from google.genai import errors, types

import vision
from estilos import formatear_foto


def _error_api(codigo, mensaje):
    return errors.APIError(codigo, {"error": {"message": mensaje}})


class PruebasReintentos(unittest.TestCase):
    def test_503_transitorio_se_recupera_en_principal(self):
        cliente = Mock()
        resultado = Mock()
        cliente.models.generate_content.side_effect = [errors.ServerError(503, {}), resultado]
        with patch.object(vision, "sleep") as esperar, self.assertLogs(vision.__name__, level="INFO"):
            self.assertIs(vision._generar_con_reintentos(cliente, ("principal", "respaldo"), []), resultado)
        esperar.assert_called_once_with(2)
        self.assertEqual([c.kwargs["model"] for c in cliente.models.generate_content.call_args_list], ["principal"] * 2)

    def test_503_prolongado_pausa_consultas_y_luego_se_recupera(self):
        respuesta = Mock(text="respuesta")
        entorno = {"GEMINI_API_KEY": "ficticia", "GEMINI_MODEL": "principal", "GEMINI_FALLBACK_MODEL": "respaldo"}
        with patch.dict("os.environ", entorno), patch.object(vision.genai, "Client") as abrir, patch.object(
            vision, "_SATURACION", ((), 0.0),
        ), patch.object(vision, "monotonic", return_value=100) as reloj, patch.object(
            vision, "sleep",
        ) as esperar, self.assertLogs(vision.__name__, level="INFO"):
            generar = abrir.return_value.__enter__.return_value.models.generate_content
            generar.side_effect = errors.ServerError(503, {})
            for _ in range(2):
                with self.assertRaisesRegex(vision.ErrorVision, "saturado"):
                    vision.consultar_gemini([], registrar_solicitud=False)
            abrir.assert_called_once()
            self.assertEqual([c.kwargs["model"] for c in generar.call_args_list], ["principal"] * 3 + ["respaldo"] * 3)
            self.assertEqual([c.args[0] for c in esperar.call_args_list], [2, 4, 2, 4])
            reloj.return_value = 121
            generar.side_effect = None
            generar.return_value = respuesta
            self.assertEqual(vision.consultar_gemini([], registrar_solicitud=False), ("respuesta", respuesta))

    def test_foto_con_429_mantiene_reintentos_y_respaldo(self):
        cliente = Mock()
        respuesta = object()
        generar = cliente.models.generate_content
        error = _error_api(429, "Clave de prueba: secreto-simulado")
        generar.side_effect = [error, error, error, respuesta]
        with patch.object(vision, "sleep") as dormir, patch.dict(
            "os.environ", {"GEMINI_API_KEY": "secreto-simulado"}
        ), self.assertLogs(vision.__name__, level="ERROR") as registro:
            resultado = vision._generar_con_reintentos(
                cliente, ("principal-secreto-simulado", "respaldo"), [],
            )
        self.assertIs(resultado, respuesta)
        self.assertEqual([c.kwargs["model"] for c in generar.call_args_list],
                         ["principal-secreto-simulado"] * 3 + ["respaldo"])
        self.assertEqual([c.args[0] for c in dormir.call_args_list], [2, 4])
        self.assertEqual(len(registro.output), 3)
        for numero, linea in enumerate(registro.output, start=1):
            self.assertIn(f"principal-***, intento {numero}, timeout", linea)
            self.assertIn("HTTP 429", linea)
            self.assertNotIn("secreto-simulado", linea)
            self.assertNotIn("Traceback", linea)

    def test_read_timeout_en_principal_usa_respaldo(self):
        error = type("ReadTimeout", (Exception,), {"__module__": "httpx2"})("lectura agotada")
        cliente = Mock()
        respuesta = object()
        generar = cliente.models.generate_content
        generar.side_effect = [error, error, error, respuesta]
        with patch.object(vision, "sleep") as dormir, self.assertLogs(
            vision.__name__, level="INFO"
        ) as registro:
            resultado = vision._generar_con_reintentos(cliente, ("principal", "respaldo"), [])
        self.assertIs(resultado, respuesta)
        self.assertEqual([c.kwargs["model"] for c in generar.call_args_list],
                         ["principal", "principal", "principal", "respaldo"])
        self.assertEqual([c.args[0] for c in dormir.call_args_list], [2, 4])
        self.assertTrue(any("httpx2.ReadTimeout" in linea and "intento 1" in linea
                            for linea in registro.output))
        self.assertTrue(any("pasa al modelo de respaldo" in linea for linea in registro.output))
        self.assertFalse(any("Traceback" in linea for linea in registro.output))

    def test_read_timeout_en_ambos_modelos_devuelve_timeout(self):
        error = type("ReadTimeout", (Exception,), {"__module__": "httpcore2"})("lectura agotada")
        cliente = Mock()
        generar = cliente.models.generate_content
        generar.side_effect = error
        with patch.object(vision, "sleep"), self.assertLogs(vision.__name__, level="INFO"):
            with self.assertRaisesRegex(vision.ErrorVision, "^timeout$"):
                vision._generar_con_reintentos(cliente, ("principal", "respaldo"), [])
        self.assertEqual([c.kwargs["model"] for c in generar.call_args_list],
                         ["principal"] * 3 + ["respaldo"] * 3)

    def test_connect_error_reintenta_el_modelo_principal(self):
        error = type("ConnectError", (Exception,), {"__module__": "httpx2"})("conexión fallida")
        cliente = Mock()
        respuesta = object()
        generar = cliente.models.generate_content
        generar.side_effect = [error, respuesta]
        with patch.object(vision, "sleep") as dormir, self.assertLogs(vision.__name__, level="INFO"):
            resultado = vision._generar_con_reintentos(cliente, ("principal", "respaldo"), [])
        self.assertIs(resultado, respuesta)
        self.assertEqual([c.kwargs["model"] for c in generar.call_args_list], ["principal"] * 2)
        dormir.assert_called_once_with(2)

    def test_error_inesperado_usa_respaldo_y_clave_invalida_no(self):
        cliente = Mock()
        respuesta = object()
        generar = cliente.models.generate_content
        with patch.object(vision, "sleep") as dormir, self.assertLogs(
            vision.__name__, level="INFO"
        ) as registro:
            generar.side_effect = [RuntimeError("fallo del cliente"), respuesta]
            self.assertIs(vision._generar_con_reintentos(cliente, ("principal", "respaldo"), []),
                          respuesta)
            self.assertEqual([c.kwargs["model"] for c in generar.call_args_list],
                             ["principal", "respaldo"])
            generar.reset_mock()
            generar.side_effect = _error_api(401, "Clave inválida")
            with self.assertRaisesRegex(vision.ErrorVision, "^clave$"):
                vision._generar_con_reintentos(cliente, ("principal", "respaldo"), [])
        dormir.assert_not_called()
        generar.assert_called_once()
        self.assertTrue(any("Traceback" in linea and "RuntimeError" in linea for linea in registro.output))

    def test_pasa_al_respaldo_si_quedan_menos_de_diez_segundos(self):
        ahora = 0
        cliente = Mock()
        respuesta = object()

        def esperar(segundos):
            nonlocal ahora
            ahora += segundos

        def generar(*, model, contents, config):
            if model == "respaldo":
                return respuesta
            esperar(config.http_options.timeout / 1000)
            if ahora == 30:
                raise _error_api(504, "Deadline exceeded")
            raise _error_api(503, "High demand")

        cliente.models.generate_content.side_effect = generar
        with patch.object(vision, "monotonic", lambda: ahora), patch.object(
            vision, "sleep", side_effect=esperar
        ) as dormir, self.assertLogs(vision.__name__, level="INFO") as registro:
            resultado = vision._generar_con_reintentos(cliente, ("principal", "respaldo"), [])

        self.assertIs(resultado, respuesta)
        dormir.assert_called_once_with(2)
        llamadas = cliente.models.generate_content.call_args_list
        self.assertEqual([llamada.kwargs["model"] for llamada in llamadas], [
            "principal", "principal", "respaldo"
        ])
        self.assertEqual([
            llamada.kwargs["config"].http_options.timeout for llamada in llamadas
        ], [30_000, 30_000, 28_000])
        self.assertTrue(any("respaldo" in mensaje and "timeout 28 s" in mensaje
                            for mensaje in registro.output))

    def test_deadline_400_reintenta_y_permite_usar_el_respaldo(self):
        cliente = Mock()
        error = _error_api(400, "Manually set deadline 4s is too short")
        respuesta = object()
        cliente.models.generate_content.side_effect = [error, error, error, respuesta]
        with patch.object(vision, "sleep"), self.assertLogs(vision.__name__, level="INFO"):
            resultado = vision._generar_con_reintentos(cliente, ("principal", "respaldo"), [])

        self.assertIs(resultado, respuesta)
        self.assertEqual(vision._motivo_api(error), "timeout")
        self.assertEqual(vision._motivo_api(_error_api(400, "Unsupported image format")), "imagen")
        self.assertEqual(vision._motivo_api(_error_api(400, "Unknown field")), "servicio")


class PruebasRespuestaVision(unittest.TestCase):
    def test_descripcion_de_foto_sin_pistas_es_breve_y_positiva(self):
        base = {"banda": None, "album": None, "confianza": "baja", "pistas": ""}
        for descripcion in (
            "Dos personas posan frente a una pared. No hay logos ni carátulas.",
            "Dos personas posan frente a una pared, pero no se observan logos ni carátulas.",
            "Dos personas posan frente a una pared sin logos ni carátulas.",
        ):
            with self.subTest(descripcion=descripcion):
                visible = formatear_foto(dict(base, descripcion=descripcion)).splitlines()[0]
                self.assertEqual(visible.rstrip("."), "📸 Dos personas posan frente a una pared")
        larga = formatear_foto(dict(base, descripcion="Dos personas " + "frente a una pared " * 12))
        self.assertLessEqual(len(larga.splitlines()[0].removeprefix("📸 ")), 120)

    def test_respuesta_vacia(self):
        with self.assertLogs(vision.__name__, level="ERROR") as registro:
            datos = vision._parsear_respuesta("")
        self.assertIsNone(datos["banda"])
        self.assertEqual(datos["confianza"], "baja")
        self.assertEqual(datos["descripcion"], "No se pudo describir.")
        self.assertIn("Texto crudo: ''", registro.output[0])

    def test_prosa_con_metadatos_y_redaccion(self):
        respuesta = types.GenerateContentResponse(candidates=[
            types.Candidate(finish_reason="STOP", safety_ratings=[
                types.SafetyRating(category="HARM_CATEGORY_HARASSMENT", blocked=False)
            ])
        ])
        texto = "No puedo identificar personas. secreto-simulado " + "x" * 500 + " FIN"
        with patch.dict("os.environ", {"GEMINI_API_KEY": "secreto-simulado"}), self.assertLogs(
            vision.__name__, level="ERROR"
        ) as registro:
            datos = vision._parsear_respuesta(texto, respuesta)
        self.assertIsNone(datos["banda"])
        self.assertEqual(datos["confianza"], "baja")
        self.assertTrue(datos["descripcion"].startswith("No puedo identificar personas. ***"))
        self.assertIn("STOP", registro.output[0])
        self.assertIn("HARM_CATEGORY_HARASSMENT", registro.output[0])
        self.assertNotIn("secreto-simulado", registro.output[0])
        self.assertNotIn(" FIN", registro.output[0])

    def test_json_en_bloque(self):
        texto = (
            "```json\n"
            '{"banda":"Metallica","album":null,"confianza":"alta",'
            '"pistas":"Logo visible","descripcion":"Una camiseta."}'
            "\n```"
        )
        datos = vision._parsear_respuesta(texto)
        self.assertEqual(datos["banda"], "Metallica")
        self.assertIsNone(datos["album"])

    def test_respuesta_bloqueada(self):
        respuesta = types.GenerateContentResponse(
            prompt_feedback={"block_reason": "SAFETY"}
        )
        with self.assertLogs(vision.__name__, level="ERROR") as registro:
            datos = vision._parsear_respuesta(None, respuesta)
        self.assertIsNone(datos["banda"])
        self.assertEqual(datos["confianza"], "baja")
        self.assertIn("SAFETY", registro.output[0])
