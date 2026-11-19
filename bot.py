import asyncio
import logging
import os
from random import sample
from contextlib import suppress
from pathlib import Path
from secrets import token_urlsafe
from urllib.parse import quote

from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatAction
from telegram.error import NetworkError, TelegramError, TimedOut
from telegram.ext import ApplicationBuilder, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters

import estilos
from acordes import obtener_acordes
from diagramas import generar_diagramas
from audio import (
    FORMATOS_AUDIO, analizar_audio, extraer_fragmento_letra, identificar_por_letra,
    validar_limites_audio,
)
from generos import (
    GENEROS, detectar_genero, detectar_intencion_recomendacion, detectar_referencia_similar,
    es_nombre_de_genero, limpiar_nombre_banda, normalizar,
)
from lastfm import (
    ErrorLastFM, buscar_artista, obtener_canciones_top, obtener_info, obtener_similares,
    obtener_top_artistas_por_genero,
)
from predict import analizar_texto
from portadas import buscar_cancion
from vision import (
    FORMATOS_IMAGEN, MAX_BYTES_IMAGEN, PRESUPUESTO_VISION, ErrorVision, analizar_imagen,
    redactar_secretos, registrar_excepcion,
)


_LOG = logging.getLogger(__name__)
_CANCIONES_ACORDES = {}
_MAX_CANCIONES_ACORDES = 512


class _FormatoConsola(logging.Formatter):
    def format(self, record):
        return redactar_secretos(super().format(record))


async def iniciar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(estilos.BIENVENIDA)


async def recomendar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    mensaje = update.effective_message
    texto = " ".join(context.args)
    if not texto:
        await mensaje.reply_text(estilos.recomendacion())
        return

    consulta = limpiar_nombre_banda(texto)
    nombre_similar = detectar_referencia_similar(consulta)
    if nombre_similar and not es_nombre_de_genero(nombre_similar):
        await _responder_similares(mensaje, nombre_similar)
        return
    etiqueta = detectar_genero(consulta)
    if etiqueta:
        await _recomendar_genero(mensaje, context, etiqueta)
        return
    await _responder_banda(mensaje, consulta, verificar=True)


_PETICIONES_OTRAS = {"otras", "otra", "mas", "dame mas"}


def _estado_generos(mensaje, context):
    if context.user_data is None:
        return {}
    conversacion = (mensaje.chat_id, mensaje.message_thread_id)
    return context.user_data.setdefault("generos", {}).setdefault(conversacion, {})


async def _cerrar_tanda_activa(update: Update, context: ContextTypes.DEFAULT_TYPE):
    mensaje = update.effective_message
    if normalizar(mensaje.text or "") not in _PETICIONES_OTRAS:
        estado = _estado_generos(mensaje, context)
        estado.pop("activo", None)


async def _cancion_para_arrancar(artista):
    try:
        canciones = await asyncio.to_thread(obtener_canciones_top, artista)
    except ErrorLastFM:
        return None
    return canciones[0] if canciones else None


async def _recomendar_genero(mensaje, context, etiqueta):
    estado = _estado_generos(mensaje, context)
    mostradas = estado.setdefault("mostradas", {}).setdefault(etiqueta, set())
    try:
        artistas = await asyncio.to_thread(obtener_top_artistas_por_genero, etiqueta)
    except ErrorLastFM as error:
        await mensaje.reply_text(estilos.ERRORES_LASTFM[str(error)])
        return
    disponibles = {
        artista.casefold(): artista for artista in artistas if artista.casefold() not in mostradas
    }
    if not disponibles:
        await mensaje.reply_text(
            estilos.GENERO_AGOTADO if artistas else estilos.GENERO_SIN_RESULTADOS
        )
        return
    tanda = sample(list(disponibles.values()), min(5, len(disponibles)))
    canciones = await asyncio.gather(*(_cancion_para_arrancar(artista) for artista in tanda))
    await mensaje.reply_text(estilos.formatear_tanda(etiqueta, zip(tanda, canciones)))
    mostradas.update(artista.casefold() for artista in tanda)
    estado["activo"] = etiqueta


async def genero(update: Update, context: ContextTypes.DEFAULT_TYPE):
    mensaje = update.effective_message
    etiqueta = detectar_genero(" ".join(context.args))
    if etiqueta:
        await _recomendar_genero(mensaje, context, etiqueta)
    else:
        await mensaje.reply_text(estilos.PEDIR_GENERO)


async def generos(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(estilos.formatear_generos(GENEROS))


def _ficha_banda(nombre):
    info = obtener_info(nombre)
    if info is None:
        return None
    similares = obtener_similares(info["nombre"])
    return estilos.formatear_banda(info, similares)


async def _redirigir_recomendacion(mensaje, context, texto, responder_artista):
    etiqueta = detectar_genero(texto)
    nombre_similar = detectar_referencia_similar(texto)
    intencion = detectar_intencion_recomendacion(texto)
    if nombre_similar and not es_nombre_de_genero(nombre_similar):
        await _responder_similares(mensaje, nombre_similar)
        return True
    if etiqueta:
        if not (intencion or es_nombre_de_genero(texto) or nombre_similar):
            try:
                artista = await asyncio.to_thread(buscar_artista, texto)
            except ErrorLastFM:
                artista = None
            if artista:
                await responder_artista(mensaje, artista)
                return True
        await _recomendar_genero(mensaje, context, etiqueta)
        return True
    if intencion:
        await mensaje.reply_text(estilos.PEDIR_GENERO)
        return True
    return False


async def _responder_banda(mensaje, nombre, *, verificar=False):
    try:
        if verificar and nombre:
            nombre = await asyncio.to_thread(buscar_artista, nombre)
        respuesta = await asyncio.to_thread(_ficha_banda, nombre) if nombre else None
    except ErrorLastFM as error:
        respuesta = estilos.ERRORES_LASTFM[str(error)]
    await mensaje.reply_text(respuesta or estilos.BANDA_NO_ENCONTRADA)


async def banda(update: Update, context: ContextTypes.DEFAULT_TYPE):
    mensaje = update.effective_message
    nombre = " ".join(context.args)
    if not nombre:
        await mensaje.reply_text("🎸 Escribe /banda seguido del nombre, parce. Ejemplo: /banda Metallica")
        return

    consulta = limpiar_nombre_banda(nombre)
    if not await _redirigir_recomendacion(mensaje, context, consulta, _responder_banda):
        await _responder_banda(mensaje, consulta)


async def similares(update: Update, context: ContextTypes.DEFAULT_TYPE):
    mensaje = update.effective_message
    nombre = " ".join(context.args)
    if not nombre:
        await mensaje.reply_text(
            "🤘 Escribe /similares seguido del nombre, parce. Ejemplo: /similares Black Sabbath"
        )
        return

    if not await _redirigir_recomendacion(mensaje, context, nombre, _responder_similares):
        await _responder_similares(mensaje, nombre)


async def _responder_similares(mensaje, nombre):
    try:
        resultados = await asyncio.to_thread(obtener_similares, nombre)
    except ErrorLastFM as error:
        respuesta = estilos.ERRORES_LASTFM[str(error)]
    else:
        respuesta = (
            estilos.BANDA_NO_ENCONTRADA if resultados is None
            else estilos.formatear_similares(nombre, resultados)
        )
    await mensaje.reply_text(respuesta)


async def responder(update: Update, context: ContextTypes.DEFAULT_TYPE):
    mensaje = update.effective_message
    texto = mensaje.text.strip()
    fragmento = extraer_fragmento_letra(texto)
    if fragmento is not None:
        await _responder_letra(mensaje, fragmento)
        return
    if normalizar(texto) in _PETICIONES_OTRAS:
        etiqueta = _estado_generos(mensaje, context).get("activo")
        if etiqueta:
            await _recomendar_genero(mensaje, context, etiqueta)
        else:
            await mensaje.reply_text(estilos.PEDIR_GENERO)
        return
    consulta = limpiar_nombre_banda(texto)
    if await _redirigir_recomendacion(mensaje, context, consulta, _responder_banda):
        return
    if consulta != texto:
        await _responder_banda(mensaje, consulta, verificar=True)
        return

    # Consultamos textos breves de una sola línea como posibles nombres.
    if texto and len(texto) <= 100 and len(texto.splitlines()) == 1:
        artista = None
        try:
            artista = await asyncio.to_thread(buscar_artista, texto)
            if artista:
                respuesta = await asyncio.to_thread(_ficha_banda, artista)
                await mensaje.reply_text(respuesta or estilos.BANDA_NO_ENCONTRADA)
                return
        except ErrorLastFM as error:
            if artista:
                await mensaje.reply_text(estilos.ERRORES_LASTFM[str(error)])
                return

    analisis = await asyncio.to_thread(analizar_texto, texto)
    await mensaje.reply_text(estilos.formatear_analisis(analisis))


async def _mostrar_escribiendo(mensaje, accion=ChatAction.TYPING):
    while True:
        # El indicador de Telegram caduca a los pocos segundos.
        try:
            await mensaje.reply_chat_action(accion)
        except TelegramError as error:
            registrar_excepcion(_LOG, "Error al mostrar el indicador de Telegram", error)
        await asyncio.sleep(4)


async def _avisar_demora_audio(mensaje):
    await asyncio.sleep(15)
    with suppress(TelegramError):
        await mensaje.reply_text(estilos.DEMORA_CANCION)


async def _identificar_cancion(mensaje, operacion, accion):
    progreso = asyncio.create_task(_mostrar_escribiendo(mensaje, accion))
    demora = asyncio.create_task(_avisar_demora_audio(mensaje))
    try:
        async with asyncio.timeout(PRESUPUESTO_VISION):
            resultado = await operacion()
    except (TimeoutError, TimedOut):
        await mensaje.reply_text(estilos.ERRORES_AUDIO["timeout"])
        return
    except ErrorVision as error:
        motivo = str(error)
        await mensaje.reply_text(
            estilos.ERRORES_AUDIO.get(motivo) or estilos.ERRORES_VISION[motivo]
        )
        return
    except TelegramError as error:
        registrar_excepcion(_LOG, "No se pudo descargar el audio de Telegram", error)
        await mensaje.reply_text(estilos.ERRORES_AUDIO["descarga_audio"])
        return
    finally:
        for tarea in (progreso, demora):
            tarea.cancel()
        await asyncio.gather(progreso, demora, return_exceptions=True)

    if not resultado["cancion"] or resultado["confianza"] == "baja":
        if resultado.get("fragmento"):
            await _mostrar_busqueda_letra(mensaje, resultado)
        else:
            await mensaje.reply_text(estilos.formatear_audio_sin_cancion(resultado.get("tipo")))
        return

    try:
        pista = await asyncio.to_thread(
            buscar_cancion, resultado["cancion"], resultado["artista"],
            titulo_exacto=resultado.get("fragmento") is not None,
        )
    except ErrorLastFM as error:
        registrar_excepcion(_LOG, "No se pudo verificar la canción", error)
        pista = None
    await _mostrar_cancion(
        mensaje, pista or resultado, resultado["confianza"], pista is not None,
        letra_verificada=resultado.get("letra_verificada"),
    )


async def _mostrar_busqueda_letra(mensaje, resultado):
    fragmento = resultado["fragmento"]
    literal = quote(fragmento, safe="")
    google = quote(f'"{fragmento}" lyrics', safe="")
    enlaces = InlineKeyboardMarkup([[
        InlineKeyboardButton("Google", url=f"https://www.google.com/search?q={google}"),
        InlineKeyboardButton("Genius", url=f"https://genius.com/search?q={literal}"),
        InlineKeyboardButton("Bandcamp", url=f"https://bandcamp.com/search?q={literal}"),
    ]])
    await mensaje.reply_text(
        estilos.formatear_letra_incierta(resultado),
        reply_markup=enlaces,
        disable_web_page_preview=True,
    )


async def _descargar_y_analizar_audio(mensaje):
    adjunto = mensaje.voice or mensaje.audio or mensaje.document
    mime = "audio/ogg" if mensaje.voice else adjunto.mime_type
    if not mime and mensaje.audio:
        mime = {
            ".mp3": "audio/mpeg", ".m4a": "audio/mp4", ".ogg": "audio/ogg", ".wav": "audio/wav"
        }.get(Path(adjunto.file_name or "").suffix.lower())
    validar_limites_audio(adjunto.file_size, getattr(adjunto, "duration", None))
    archivo = await adjunto.get_file()
    datos = await archivo.download_as_bytearray()
    return await asyncio.to_thread(analizar_audio, bytes(datos), mime)


async def audio_recibido(update: Update, context: ContextTypes.DEFAULT_TYPE):
    mensaje = update.effective_message
    await _identificar_cancion(
        mensaje, lambda: _descargar_y_analizar_audio(mensaje), ChatAction.RECORD_VOICE
    )


async def _responder_letra(mensaje, fragmento):
    if not fragmento:
        await mensaje.reply_text(estilos.PEDIR_CANCION)
        return
    await _identificar_cancion(
        mensaje, lambda: asyncio.to_thread(identificar_por_letra, fragmento), ChatAction.TYPING
    )


async def cancion(update: Update, context: ContextTypes.DEFAULT_TYPE):
    mensaje = update.effective_message
    consulta = " ".join(context.args)
    fragmento = extraer_fragmento_letra(consulta)
    if fragmento is not None or not consulta:
        await _responder_letra(mensaje, fragmento)
        return
    try:
        pista = await asyncio.to_thread(buscar_cancion, consulta, titulo_exacto=True)
    except ErrorLastFM as error:
        await mensaje.reply_text(estilos.ERRORES_LASTFM[str(error)])
        return
    if pista:
        await _mostrar_cancion(mensaje, pista, "alta", True)
    else:
        await _responder_letra(mensaje, consulta)


def _boton_acordes(cancion, artista):
    # Telegram limita callback_data a 64 bytes y los nombres pueden ser más largos.
    if len(_CANCIONES_ACORDES) >= _MAX_CANCIONES_ACORDES:
        del _CANCIONES_ACORDES[next(iter(_CANCIONES_ACORDES))]
    clave = f"acordes:{token_urlsafe(9)}"
    _CANCIONES_ACORDES[clave] = (cancion, artista)
    return InlineKeyboardMarkup([[InlineKeyboardButton("🎸 Ver acordes", callback_data=clave)]])


def _enlaces_acordes(cancion, artista):
    tema = " ".join(filter(None, (artista, cancion)))
    sitios = (
        ("Ultimate Guitar", f"site:ultimate-guitar.com {tema} chords"),
        ("Songsterr", f"site:songsterr.com {tema}"),
        ("Cifra Club", f"site:cifraclub.com {tema}"),
        ("Ver letra", f"site:letras.com {tema}"),
    )
    botones = [
        InlineKeyboardButton(nombre, url="https://www.google.com/search?q=" + quote(consulta, safe=""))
        for nombre, consulta in sitios
    ]
    return InlineKeyboardMarkup([botones[:2], botones[2:]])


async def _responder_acordes(mensaje, cancion, artista=None):
    imagen = None
    try:
        async with asyncio.timeout(PRESUPUESTO_VISION):
            datos = await asyncio.to_thread(obtener_acordes, cancion, artista)
    except (ErrorVision, TimeoutError):
        texto = estilos.ERROR_ACORDES
    else:
        texto = estilos.formatear_acordes(cancion, artista, datos)
        if datos:
            acordes = [acorde for seccion in datos["secciones"].values() for acorde in seccion]
            imagen, omitidos = await asyncio.to_thread(generar_diagramas, acordes)
            if omitidos:
                texto += "\n\nSin diagrama: " + ", ".join(omitidos)
    await mensaje.reply_text(
        texto, reply_markup=_enlaces_acordes(cancion, artista), disable_web_page_preview=True,
    )
    if imagen is None:
        return
    afinacion = datos["afinacion"]
    pie = "🎸 Posiciones en afinación estándar"
    if afinacion:
        nombre = " ".join(afinacion.replace("-", " ").split())
        if nombre not in ("Estándar", "Estándar en E", "E A D G B E"):
            pie += f"\nOjo: la canción usa afinación {afinacion}; las posiciones son para afinación estándar"
    with imagen:
        try:
            await mensaje.reply_photo(photo=imagen, caption=pie)
        except TelegramError as error:
            registrar_excepcion(_LOG, "No se pudo enviar la imagen de acordes", error)


async def acordes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    mensaje = update.effective_message
    consulta = " ".join(context.args)
    artista, separador, cancion = consulta.partition(" - ")
    if not separador:
        artista, cancion = None, consulta
    if not cancion:
        await mensaje.reply_text(estilos.PEDIR_ACORDES)
        return
    if artista is None:
        try:
            pista = await asyncio.to_thread(buscar_cancion, cancion, titulo_exacto=True)
        except ErrorLastFM:
            pista = None
        if pista:
            cancion, artista = pista["cancion"], pista["artista"]
    await _responder_acordes(mensaje, cancion, artista)


async def acordes_boton(update: Update, context: ContextTypes.DEFAULT_TYPE):
    consulta = update.callback_query
    tema = _CANCIONES_ACORDES.get(consulta.data)
    respuesta = getattr(consulta.message, "reply_text", None)
    if tema is None or respuesta is None:
        await consulta.answer(estilos.BOTON_ACORDES_VENCIDO)
        return
    await consulta.answer()
    await _responder_acordes(consulta.message, *tema)


def _ficha_artista_cancion(artista):
    try:
        info = obtener_info(artista)
    except ErrorLastFM:
        info = None
    similares = obtener_similares(artista)
    return (estilos.formatear_banda(info, similares) if info else
            estilos.formatear_similares(artista, similares))


async def _mostrar_cancion(mensaje, pista, confianza, en_catalogo, *, letra_verificada=None):
    texto = estilos.formatear_cancion(
        pista, confianza, en_catalogo, letra_verificada=letra_verificada,
    )
    boton = _boton_acordes(pista["cancion"], pista["artista"])
    if pista.get("portada"):
        try:
            await mensaje.reply_photo(photo=pista["portada"], caption=texto, reply_markup=boton)
        except TelegramError as error:
            registrar_excepcion(_LOG, "No se pudo enviar la portada", error)
            await mensaje.reply_text(texto, reply_markup=boton)
    else:
        await mensaje.reply_text(texto, reply_markup=boton)
    try:
        ficha = await asyncio.to_thread(_ficha_artista_cancion, pista["artista"])
    except ErrorLastFM:
        return
    await mensaje.reply_text(ficha)


async def _analizar_foto(mensaje):
    if mensaje.photo:
        adjunto = max(mensaje.photo, key=lambda foto: foto.width * foto.height)
        mime_type = "image/jpeg"
    else:
        adjunto = mensaje.document
        mime_type = adjunto.mime_type

    if adjunto.file_size and adjunto.file_size > MAX_BYTES_IMAGEN:
        raise ErrorVision("tamano")
    try:
        archivo = await adjunto.get_file()
        imagen = await archivo.download_as_bytearray()
    except TimedOut as error:
        registrar_excepcion(_LOG, "Tiempo de espera agotado al descargar la foto de Telegram", error)
        raise ErrorVision("timeout") from None
    except NetworkError as error:
        registrar_excepcion(_LOG, "Error de conexión al descargar la foto de Telegram", error)
        raise ErrorVision("conexion") from None
    except TelegramError as error:
        registrar_excepcion(_LOG, "Error al descargar la foto de Telegram", error)
        raise ErrorVision("descarga") from None

    try:
        async with asyncio.timeout(PRESUPUESTO_VISION):
            return await asyncio.to_thread(
                analizar_imagen, bytes(imagen), mime_type, caption=mensaje.caption
            )
    except TimeoutError as error:
        registrar_excepcion(_LOG, "Se agotó el tiempo total del análisis de la foto", error)
        raise ErrorVision("timeout") from None


async def foto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    mensaje = update.effective_message
    escribiendo = asyncio.create_task(_mostrar_escribiendo(mensaje))
    try:
        resultado = await _analizar_foto(mensaje)
        respuesta = estilos.formatear_foto(resultado)
        if resultado["banda"]:
            try:
                ficha = await asyncio.to_thread(_ficha_banda, resultado["banda"])
            except ErrorLastFM as error:
                registrar_excepcion(_LOG, "Error de Last.fm al preparar la ficha de la foto", error)
                ficha = estilos.ERRORES_LASTFM[str(error)]
            respuesta += "\n\n" + (ficha or estilos.BANDA_NO_ENCONTRADA)
    except ErrorVision as error:
        motivo = str(error)
        # Los fallos de la consulta ya se registran con el detalle de cada intento.
        if motivo not in ("cuota", "timeout", "conexion", "servicio"):
            registrar_excepcion(_LOG, f"No se pudo procesar la foto: {motivo}", error, con_traza=False)
        respuesta = estilos.ERRORES_VISION[motivo]
    except Exception as error:
        registrar_excepcion(_LOG, "Error inesperado al procesar la foto", error)
        raise
    finally:
        escribiendo.cancel()
        with suppress(asyncio.CancelledError):
            await escribiendo

    try:
        await mensaje.reply_text(respuesta)
    except TelegramError as error:
        registrar_excepcion(_LOG, "Error al enviar la respuesta de la foto a Telegram", error)
        raise


async def manejar_error(update: object, context: ContextTypes.DEFAULT_TYPE):
    # Las excepciones de red pueden contener URLs con credenciales.
    if isinstance(update, Update) and update.effective_message:
        try:
            await update.effective_message.reply_text("⚠️ Se nos cruzó un cable, parce. Intenta de nuevo.")
        except TelegramError:
            pass


def main():
    consola = logging.StreamHandler()
    consola.setFormatter(_FormatoConsola("%(levelname)s %(name)s: %(message)s"))
    logging.basicConfig(level=logging.INFO, handlers=[consola], force=True)
    # Las trazas HTTP de Telegram pueden incluir el token en la URL.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    load_dotenv(Path(__file__).with_name(".env"))
    _LOG.info("GEMINI_MODEL: %s", os.getenv("GEMINI_MODEL", "").strip() or "no definido")
    _LOG.info("GEMINI_FALLBACK_MODEL: %s", os.getenv("GEMINI_FALLBACK_MODEL", "").strip() or "no definido")
    token = os.getenv("TELEGRAM_TOKEN")
    if not token:
        raise SystemExit("Falta configurar TELEGRAM_TOKEN.")

    try:
        app = ApplicationBuilder().token(token).build()
        app.add_handler(MessageHandler(filters.ALL, _cerrar_tanda_activa), group=-1)
        app.add_handler(CommandHandler("start", iniciar))
        app.add_handler(CommandHandler("banda", banda))
        app.add_handler(CommandHandler("similares", similares))
        app.add_handler(CommandHandler("recomendar", recomendar))
        app.add_handler(CommandHandler("genero", genero))
        app.add_handler(CommandHandler("generos", generos))
        app.add_handler(CommandHandler("cancion", cancion))
        app.add_handler(CommandHandler("acordes", acordes))
        app.add_handler(CallbackQueryHandler(acordes_boton, pattern=r"^acordes:[\w-]+$"))
        filtro_fotos = filters.PHOTO
        for mime_type in FORMATOS_IMAGEN:
            filtro_fotos |= filters.Document.MimeType(mime_type)
        app.add_handler(MessageHandler(filtro_fotos, foto))
        filtro_audio = filters.VOICE | filters.AUDIO
        for mime_type in FORMATOS_AUDIO:
            filtro_audio |= filters.Document.MimeType(mime_type)
        app.add_handler(MessageHandler(filtro_audio, audio_recibido))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, responder))
        app.add_error_handler(manejar_error)
        print("🤘 Moshpit Bot al aire.")
        app.run_polling()
    except TelegramError:
        raise SystemExit("Telegram no pudo iniciar el bot. Revisa la conexión y TELEGRAM_TOKEN.") from None


if __name__ == "__main__":
    main()
