"""Contexto y reutilización en curso para las operaciones de Gemini desde Telegram."""

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from contextvars import ContextVar, copy_context
from dataclasses import dataclass, field
from functools import partial
from hashlib import blake2s
from secrets import token_bytes, token_hex
from threading import Event
from time import monotonic


_LOG = logging.getLogger(__name__)
_CLAVE_REGISTRO = token_bytes(16)
_EN_CURSO = {}
_EJECUTOR = ThreadPoolExecutor(max_workers=12, thread_name_prefix="moshpit-gemini")


@dataclass
class OperacionGemini:
    nombre: str
    actor: str
    limite: float
    id: str = field(default_factory=lambda: token_hex(6))
    cancelada: Event = field(default_factory=Event)


@dataclass
class TrabajoGemini:
    operacion: OperacionGemini
    tarea: asyncio.Task
    interesados: int = 0


OPERACION = ContextVar("operacion_gemini", default=None)


async def _procesar(operacion, funcion, args, kwargs):
    contexto = OPERACION.set(operacion)
    try:
        # Las esperas de Gemini no consumen el pool que usan Last.fm y los demás handlers.
        llamada = partial(funcion, *args, **kwargs)
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(_EJECUTOR, copy_context().run, llamada)
    finally:
        operacion.cancelada.set()
        OPERACION.reset(contexto)


async def ejecutar_gemini(nombre, mensaje, presupuesto, funcion, *args, usuario=None, **kwargs):
    """Comparte solo trabajo en curso con el mismo usuario, chat y argumentos."""
    autor = usuario if usuario is not None else getattr(mensaje, "from_user", None)
    usuario_id = getattr(autor, "id", None)
    chat_id = getattr(mensaje, "chat_id", None)
    actor = "sin-identificar"
    clave = None
    if usuario_id is not None and chat_id is not None:
        actor = blake2s(f"{chat_id}:{usuario_id}".encode(), key=_CLAVE_REGISTRO, digest_size=6).hexdigest()
        # El identificador de audio/foto se calcula con hash. La entrada no se registra.
        entrada = tuple(blake2s(v).digest() if isinstance(v, bytes) else v for v in args)
        clave = (asyncio.get_running_loop(), chat_id, usuario_id, nombre, entrada, tuple(kwargs.items()))

    trabajo = _EN_CURSO.get(clave) if clave else None
    if trabajo is None or trabajo.tarea.done():
        operacion = OperacionGemini(nombre, actor, monotonic() + presupuesto)
        tarea = asyncio.create_task(_procesar(operacion, funcion, args, kwargs))
        trabajo = TrabajoGemini(operacion, tarea)
        if clave:
            _EN_CURSO[clave] = trabajo
    else:
        _LOG.info("Gemini op=%s func=%s actor=%s: se reutiliza trabajo pendiente",
                  trabajo.operacion.id, nombre, actor)
    trabajo.interesados += 1
    try:
        return await asyncio.shield(trabajo.tarea)
    finally:
        trabajo.interesados -= 1
        if not trabajo.interesados:
            if clave and _EN_CURSO.get(clave) is trabajo:
                del _EN_CURSO[clave]
            if not trabajo.tarea.done():
                trabajo.operacion.cancelada.set()
                trabajo.tarea.cancel()
            elif not trabajo.tarea.cancelled():
                trabajo.tarea.exception()
