import asyncio
import os
import unittest
from threading import Event, Lock
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from google.genai.errors import ServerError
from telegram.constants import ChatAction

import bot
import estilos
import vision
from gemini_operaciones import OPERACION, ejecutar_gemini


def _mensaje(usuario):
    return SimpleNamespace(chat_id=10, from_user=SimpleNamespace(id=usuario))


def _consultar(entrada):
    return vision.consultar_gemini([entrada], registrar_solicitud=False)[0]


class PruebasConcurrenciaGemini(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.enterContext(patch.dict(os.environ, {
            "GEMINI_API_KEY": "clave-ficticia",
            "GEMINI_MODEL": "principal",
            "GEMINI_FALLBACK_MODEL": "respaldo",
            "GEMINI_MAX_CONCURRENTES": "1",
        }))
        self.enterContext(patch.object(vision, "_SATURACION", ((), 0.0)))
        self.enterContext(patch.object(vision, "sleep"))
        self.cliente = MagicMock()
        self.cliente.__enter__.return_value = self.cliente
        self.generar = self.cliente.models.generate_content
        self.enterContext(patch.object(vision.genai, "Client", return_value=self.cliente))
        self.liberar = Event()
        self.addCleanup(self.liberar.set)

    def lanzar(self, usuario, entrada, nombre="acordes"):
        return asyncio.create_task(ejecutar_gemini(nombre, _mensaje(usuario), vision.PRESUPUESTO_VISION, _consultar, entrada))

    async def esperar(self, condicion):
        async with asyncio.timeout(4):
            while not condicion():
                await asyncio.sleep(0.01)

    async def test_dedup_por_usuario_y_funcion_con_respuestas_propias(self):
        os.environ["GEMINI_MAX_CONCURRENTES"] = "2"
        op = []
        lock = Lock()

        def generar(**kwargs):
            actual = OPERACION.get()
            with lock:
                op.append((actual.id, actual.actor))
            self.liberar.wait(4)
            return SimpleNamespace(text=f"{actual.actor}:{kwargs['contents'][0]}")

        self.generar.side_effect = generar
        with self.assertLogs(level="INFO") as registros:
            a = self.lanzar(1, "mismo tema")
            repetida = self.lanzar(1, "mismo tema")
            b = self.lanzar(2, "mismo tema")
            await self.esperar(lambda: len(op) == 2)
            self.liberar.set()
            ra, repetida, rb = await asyncio.gather(a, repetida, b)
        self.assertEqual(ra, repetida)
        self.assertNotEqual(ra, rb)
        self.assertEqual(len(set(op)), 2)
        self.assertTrue(any("se reutiliza" in linea for linea in registros.output))
        self.assertTrue(any("op=" in linea and "llamada=" in linea and "func=acordes" in linea
                            for linea in registros.output))
        self.assertFalse(any("mismo tema" in linea or "clave-ficticia" in linea for linea in registros.output))

    async def test_en_cola_respeta_pausa_503_y_servicio_se_recupera(self):
        entrada = Event()

        def falla(**kwargs):
            entrada.set()
            self.liberar.wait(4)
            raise ServerError(503, {})

        self.generar.side_effect = falla
        with self.assertLogs(vision.__name__, level="INFO") as registros:
            a = self.lanzar(1, "uno")
            await self.esperar(entrada.is_set)
            b = self.lanzar(2, "dos", "foto")
            await self.esperar(lambda: any("en cola" in r.getMessage() for r in registros.records))
            self.liberar.set()
            resultados = await asyncio.gather(a, b, return_exceptions=True)
        self.assertEqual([str(r) for r in resultados], ["saturado", "saturado"])
        self.assertEqual([c.kwargs["model"] for c in self.generar.call_args_list],
                         ["principal"] * 3 + ["respaldo"] * 3)
        self.assertEqual({c.kwargs["contents"][0] for c in self.generar.call_args_list}, {"uno"})
        self.generar.side_effect = None
        self.generar.return_value = SimpleNamespace(text="recuperado")
        with patch.object(vision, "_SATURACION", (("principal", "respaldo"), 0)):
            self.assertEqual(await self.lanzar(2, "dos", "foto"), "recuperado")

    async def test_pausa_detiene_reintento_de_otra_llamada_activa(self):
        os.environ["GEMINI_MAX_CONCURRENTES"] = "2"
        inicio = Event()
        def generar(**kwargs):
            if kwargs["contents"] == ["lenta"]:
                inicio.set()
                self.liberar.wait(4)
            raise ServerError(503, {})

        self.generar.side_effect = generar
        with self.assertLogs(vision.__name__):
            lenta = self.lanzar(1, "lenta")
            await self.esperar(inicio.is_set)
            with self.assertRaisesRegex(vision.ErrorVision, "saturado"):
                await self.lanzar(2, "rápida")
            self.liberar.set()
            with self.assertRaisesRegex(vision.ErrorVision, "saturado"):
                await lenta
        self.assertEqual(sum(c.kwargs["contents"] == ["lenta"] for c in self.generar.call_args_list), 1)

    async def test_cancelar_un_interesado_conserva_el_resultado_para_el_otro(self):
        inicio = Event()

        def generar(**kwargs):
            inicio.set()
            self.liberar.wait(4)
            return SimpleNamespace(text="mismo resultado")

        self.generar.side_effect = generar
        with self.assertLogs(level="INFO") as registros:
            solicitante = self.lanzar(1, "tema")
            await self.esperar(inicio.is_set)
            duplicada = self.lanzar(1, "tema")
            await self.esperar(lambda: any("se reutiliza" in r.getMessage() for r in registros.records))
            solicitante.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await solicitante
            self.liberar.set()
            self.assertEqual(await duplicada, "mismo resultado")
        self.generar.assert_called_once()

    async def test_cancelar_en_cola_y_activa_no_envia_ni_reintenta(self):
        inicio = Event()

        def generar(**kwargs):
            if kwargs["contents"] == ["activa"]:
                inicio.set()
                self.liberar.wait(4)
                raise ServerError(503, {})
            return SimpleNamespace(text="otra canción")

        self.generar.side_effect = generar
        with self.assertLogs(vision.__name__) as registros:
            activa = self.lanzar(1, "activa")
            await self.esperar(inicio.is_set)
            cola = self.lanzar(2, "solo en cola")
            await self.esperar(lambda: any("en cola" in r.getMessage() for r in registros.records))
            for tarea in (cola, activa):
                tarea.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await tarea
            recuperada = self.lanzar(3, "otra")
            self.liberar.set()
            self.assertEqual(await recuperada, "otra canción")
        self.assertEqual([c.kwargs["contents"] for c in self.generar.call_args_list],
                         [["activa"], ["otra"]])

    async def test_cola_caduca_con_error_sin_llamar_al_modelo(self):
        inicio = Event()

        def generar(**kwargs):
            inicio.set()
            self.liberar.wait(4)
            return SimpleNamespace(text="primer resultado")

        self.generar.side_effect = generar
        with patch.object(vision, "_ESPERA_COLA_GEMINI", 0.06):
            primera = self.lanzar(1, "primera")
            await self.esperar(inicio.is_set)
            with self.assertRaisesRegex(vision.ErrorVision, "gemini_ocupado"):
                await self.lanzar(2, "expirada")
            self.liberar.set()
            self.assertEqual(await primera, "primer resultado")
        self.generar.assert_called_once()


    async def test_telegram_responde_a_cada_usuario_y_detiene_el_progreso(self):
        mensajes = [SimpleNamespace(reply_text=AsyncMock()) for _ in range(3)]
        progreso_detenido = []

        async def progreso(mensaje, _accion):
            try:
                await asyncio.Future()
            finally:
                progreso_detenido.append(mensaje)

        async def buscar(titulo):
            await asyncio.sleep(0)
            return {"cancion": None, "confianza": "baja",
                    "alternativas": [{"cancion": titulo, "artista": "Banda"}]}

        async def no_responde():
            await asyncio.Future()

        with patch.object(bot, "_mostrar_escribiendo", side_effect=progreso), patch.object(
            bot, "PRESUPUESTO_VISION", 0.05,
        ):
            await asyncio.gather(
                bot._identificar_cancion(mensajes[0], lambda: buscar("Tema uno"), ChatAction.TYPING, es_audio=True),
                bot._identificar_cancion(mensajes[1], lambda: buscar("Tema dos"), ChatAction.TYPING, es_audio=True),
                bot._identificar_cancion(mensajes[2], no_responde, ChatAction.TYPING, es_audio=True),
            )
        self.assertIn("Tema uno", mensajes[0].reply_text.await_args.args[0])
        self.assertNotIn("Tema dos", mensajes[0].reply_text.await_args.args[0])
        self.assertIn("Tema dos", mensajes[1].reply_text.await_args.args[0])
        mensajes[2].reply_text.assert_awaited_once_with(estilos.ERRORES_AUDIO["timeout"])
        self.assertCountEqual([id(m) for m in progreso_detenido], [id(m) for m in mensajes])
