import re
from random import choice


INTERPRETACIONES = {
    "reclamo": [
        "📢 La persona se siente ignorada y reclama tu atención.",
        "😤 Hay frustración porque esperaba una respuesta y no llegó.",
        "💢 Percibe que la estás descuidando o ignorando.",
    ],
    "celos": [
        "😒 Siente inseguridad y teme que haya un tercero.",
        "🔍 Necesita confirmar que sigue siendo importante para ti.",
        "🤨 Desconfía de tus acciones o amistades.",
    ],
    "agresion": [
        "⚠️ Está muy enojado y busca herir o provocar.",
        "🔥 El conflicto puede escalar si respondes de forma similar.",
        "💥 Hay agresividad que debes manejar con cuidado.",
    ],
    "tristeza": [
        "😢 Está pasando por un momento difícil o de dolor.",
        "🫂 Necesita apoyo emocional y contención.",
        "💔 Hay vulnerabilidad, no lo ignores.",
    ],
    "manipulacion": [
        "🎭 Está usando culpa o condiciones para obtener lo que quiere.",
        "⛓️ Quiere presionarte emocionalmente para que actúes.",
        "⚠️ Ten cuidado con responder desde la culpa.",
    ],
    "control": [
        "🔒 Quiere saber todo lo que haces y con quién.",
        "👁️ Hay una necesidad excesiva de controlarte.",
        "📱 No confía plenamente en ti.",
    ],
    "ruptura": [
        "💔 Está considerando seriamente terminar la relación.",
        "😩 Hay agotamiento emocional acumulado.",
        "🚪 Quiere distancia o poner un punto final.",
    ],
    "desinteres": [
        "😐 No muestra interés en seguir la conversación.",
        "❄️ Hay distancia emocional o está ocupado.",
        "📉 Probablemente no quiere profundizar.",
    ],
    "afecto": [
        "💕 Está expresando cariño y buenos sentimientos.",
        "🤗 Quiere acercarse y fortalecer el vínculo.",
        "🌸 Hay conexión emocional positiva.",
    ],
    "curiosidad": [
        "🤔 Tiene interés genuino en ti y tu vida.",
        "💬 Quiere iniciar o mantener una conversación.",
        "🔍 Busca conocer más sobre ti.",
    ],
    "normal": [
        "💬 Es una conversación cotidiana sin carga emocional.",
        "✅ No hay señales de conflicto ni urgencia.",
        "📝 Todo fluye con normalidad.",
    ],
}

# SUGERENCIAS: Cómo responder al mensaje (para TI)
SUGERENCIAS = {
    "reclamo": [
        "🗣️ Responde: 'Entiendo que te sientes ignorado, no fue mi intención. Hablemos.'",
        "⏸️ Reconoce su molestia antes de dar explicaciones: 'Tienes razón, no respondí rápido.'",
        "🤝 Pregunta: '¿Qué puedo hacer para que no te sientas así?'",
    ],
    "celos": [
        "🧠 Responde con calma: 'No hay nadie más, pero entiendo tu inseguridad.'",
        "💬 Valida su emoción: 'Veo que esto te preocupa, hablemos sin pelear.'",
        "🔐 Reafirma: 'Eres importante para mí. ¿Podemos hablar de esto tranquilos?'",
    ],
    "agresion": [
        "🌬️ No respondas con agresión. Di: 'Esto se está poniendo feo, hablemos más tarde.'",
        "🚫 Establece límite: 'No voy a responder si me hablas así.'",
        "⏰ Tómate un tiempo: 'Mejor hablamos cuando estemos más calmados.'",
    ],
    "tristeza": [
        "👥 Responde: 'Estoy aquí para ti. ¿Quieres hablar de lo que pasa?'",
        "💬 Muestra apoyo: 'Lamento que te sientas así, cuentas conmigo.'",
        "🤗 Ofrece compañía: '¿Necesitas desahogarte o prefieres distraerte?'",
    ],
    "manipulacion": [
        "🚩 No cedas por culpa. Di: 'No me gusta que me condiciones así.'",
        "🗣️ Marca límite: 'Si me quieres pedir algo, hazlo sin chantajes.'",
        "⚖️ Responde neutro: 'Te escucho, pero no voy a reaccionar desde la culpa.'",
    ],
    "control": [
        "🤝 Responde: 'Confío en ti y espero que tú confíes en mí.'",
        "📱 Marca espacio: 'Está bien que quieras saber, pero también necesito mi espacio.'",
        "🧘 Tranquiliza: 'No pasa nada, pero no puedo responder todo el tiempo.'",
    ],
    "ruptura": [
        "💬 Responde: 'Hablemos esto en persona, es importante.'",
        "🤔 Pide tiempo: 'No tomemos decisiones en caliente. Hablemos mañana.'",
        "🕊️ Si es firme: 'Lo respeto si es tu decisión, pero me duele.'",
    ],
    "desinteres": [
        "🧐 Pregunta: '¿Estás bien? Parece que no tienes ganas de hablar.'",
        "💬 Dale espacio: 'Cuando quieras hablar, aquí estoy.'",
        "⏸️ No insistas: 'Ok, hablamos cuando tengas tiempo.'",
    ],
    "afecto": [
        "🌸 Corresponde: 'Yo también te quiero mucho ❤️'",
        "💌 Agradece: 'Qué bonito mensaje, gracias por tus palabras.'",
        "🎁 Sorprende: 'Me hiciste el día, te quiero igual.'",
    ],
    "curiosidad": [
        "🔍 Responde con entusiasmo: 'Todo bien, ¿y tú? Cuéntame.'",
        "💬 Aprovecha: 'Justo pensaba en ti. ¿Qué has hecho?'",
        "🌟 Sigue la conversación: 'Me alegra que preguntes, te cuento...'",
    ],
    "normal": [
        "👍 Responde con naturalidad: 'Todo bien, ¿y tú?'",
        "💬 Sigue la conversación: 'Qué bueno, seguimos hablando.'",
        "🌟 Mantén el tono: 'Genial, cualquier cosa me dices.'",
    ],
}


BIENVENIDA = (
    "🤘 ¡Qué más, parce! Soy Moshpit Bot. Cuéntame cómo te sientes y "
    "te pongo bandas para ese ánimo. Si me escribes el nombre de una banda, "
    "te cuento sobre ella y quiénes suenan parecido. "
    "📸 Mándame una foto de una portada, una camiseta o un póster y busco pistas de la banda o el álbum.\n\n"
    "🎙️ Mándame una nota de voz o un audio y busco la canción y su portada.\n\n"
    "🎸 /banda <nombre> · ficha y bandas similares\n"
    "🔥 /similares <nombre> · bandas parecidas\n"
    "🥁 /recomendar · una banda y un dato curioso\n"
    "🎧 /genero <nombre> · bandas y canciones para arrancar\n"
    "🤘 /generos · géneros disponibles\n\n"
    "💿 /cancion <título o fragmento> · busca una canción\n"
    "🎸 /acordes <banda - canción> · acordes principales\n\n"
    "También puedes decirme «algo de black metal» y luego «otras» para otra tanda."
)
BANDA_NO_ENCONTRADA = (
    "🎸 Parce, esa banda no salió al escenario. Revisa el nombre o prueba con un género "
    "como «black metal». En /generos está la lista."
)
PEDIR_GENERO = (
    "🎸 ¿Qué género te gusta, parce? Prueba con «black metal», «thrash», "
    "«rock alternativo» o mira la lista con /generos."
)
GENERO_AGOTADO = "🥁 Parce, ya sonaron todas las bandas de esa lista. ¿Probamos otro género? Mira /generos."
GENERO_SIN_RESULTADOS = "🎸 Last.fm dejó ese escenario vacío, parce. Prueba con otro género de /generos."
FRASES_TANDA = (
    "🤘 Parce, pon esta tanda de {genero} a todo volumen:",
    "🔥 Hermano, aquí va una tanda de {genero}:",
)


def formatear_tanda(etiqueta, recomendaciones):
    encabezado = choice(FRASES_TANDA).format(genero=etiqueta)
    lista = "\n".join(
        f"🎸 {banda} · Arranca con «{cancion}»" if cancion else f"🎸 {banda}"
        for banda, cancion in recomendaciones
    )
    return (
        f"{encabezado}\n{lista}\n\n"
        "Escribe /banda <nombre> para conocer más o pide «otras» para otra tanda."
    )


def formatear_generos(etiquetas):
    return "🎧 ¿Qué ponemos a sonar, parce?\n\n" + ", ".join(etiquetas) + "\n\nPrueba /genero black metal."


ERRORES_LASTFM = {
    "configuracion": "🔌 Parce, falta configurar bien la conexión con Last.fm.",
    "limite": "🥁 Last.fm pidió un descanso entre canciones, parce. Intenta en un rato.",
    "timeout": "⏱️ Last.fm se quedó afinando. Prueba de nuevo en un rato, parce.",
    "conexion": "🔌 Se soltó el cable con Last.fm, hermano. Intenta de nuevo en un rato.",
    "servicio": "🎚️ Last.fm anda con problemas de sonido. Prueba más tarde, parce.",
}

RECOMENDACIONES = (
    ("Black Sabbath", "Antes de llamarse Black Sabbath, la banda se presentaba como Earth."),
    ("Queen", "Brian May construyó su guitarra Red Special junto con su papá."),
    ("Metallica", "Cliff Burton tocó con distorsión el solo de bajo de «(Anesthesia) Pulling Teeth»."),
    ("Led Zeppelin", "En «Whole Lotta Love», Jimmy Page usó un theremín."),
)

EMOCIONES = {
    "enojo": ("Rabia", "🔥", "Slayer, Pantera y Rage Against the Machine", (
        "Parce, suelta esa rabia con unos riffs pesados.",
        "Hermano, para ese ánimo van guitarras con toda la distorsión.",
    )),
    "tristeza": ("Tristeza", "🎧", "Nirvana, Pink Floyd y Linkin Park", (
        "Parce, hay canciones que hacen compañía cuando el día pesa.",
        "Hermano, ponte los audífonos y dale espacio a lo que sientes.",
    )),
    "positivo": ("Alegría", "🤘", "AC/DC, Queen y Guns N' Roses", (
        "¡Qué chimba, parce! Eso pide rock a todo volumen.",
        "¡De una, hermano! Celebra con un coro para cantar a grito herido.",
    )),
    "miedo": ("Miedo o ansiedad", "🖤", "Black Sabbath y Tool", (
        "Respira, parce. Aquí van unos riffs para acompañar el momento.",
        "Tómalo con calma, hermano. Prueba estas bandas a tu ritmo.",
    )),
    "neutral": ("Neutral", "🎸", "Led Zeppelin y Metallica", (
        "Parce, cualquier rato es bueno para poner un clásico.",
        "Todo tranqui, hermano. ¿Le subimos un poquito al amplificador?",
    )),
    "sorpresa": ("Sorpresa", "⚡", "AC/DC, Queen y Guns N' Roses", (
        "¡Uy, parce! Ese giro pide una entrada de batería.",
        "Hermano, eso sí que llegó sin aviso. Ponle un buen riff.",
    )),
    "disgusto": ("Disgusto", "💥", "Slayer, Pantera y Rage Against the Machine", (
        "Qué mal sabor, parce. Cambiemos el ambiente con unas guitarras.",
        "Hermano, a eso le cae bien una tanda de riffs pesados.",
    )),
}


def formatear_analisis(analisis):
    emocion, toxicidad, ghosting, riesgo, intencion, _ = analisis
    etiqueta, emoji, bandas, frases = EMOCIONES.get(emocion, EMOCIONES["neutral"])
    toxicidad_visible = "🔴 Alta" if toxicidad == "si" else "🟢 Baja"
    bloques = ghosting // 10
    barra = "█" * bloques + "░" * (10 - bloques)
    return (
        f"🎚️ Análisis del mensaje\n"
        f"{emoji} Emoción: {etiqueta}\n"
        f"Toxicidad: {toxicidad_visible}\n"
        f"Intención: {intencion.capitalize()}\n"
        f"Ghosting: {barra} {ghosting}%\n"
        f"Riesgo: {riesgo}\n\n"
        f"Qué significa: {choice(INTERPRETACIONES.get(intencion, INTERPRETACIONES['normal']))}\n"
        f"Cómo responder: {choice(SUGERENCIAS.get(intencion, SUGERENCIAS['normal']))}\n\n"
        f"{choice(frases)}\n🎸 Para escuchar: {bandas}"
    )


def formatear_similares(nombre, similares):
    if not similares:
        return f"🎸 Parce, por ahora Last.fm no tiene bandas similares para {nombre}."
    lista = "\n".join(f"• {banda}" for banda in similares)
    return f"🤘 Si te gusta {nombre}, pon estas bandas a sonar:\n{lista}"


def formatear_banda(info, similares):
    tags = ", ".join(info["tags"]) or "Sin etiquetas en Last.fm"
    descripcion = info["descripcion"] or "Last.fm todavía no tiene una descripción."
    return (
        f"🎸 {info['nombre']}\n"
        f"🏷️ Tags: {tags}\n"
        f"{descripcion}\n\n"
        f"{formatear_similares(info['nombre'], similares)}"
    )


def recomendacion():
    banda, dato = choice(RECOMENDACIONES)
    return f"🤘 Parce, ¡pon a {banda} a todo volumen!\n🥁 Dato curioso: {dato}"


GEMINI_SATURADO = "Gemini está temporalmente saturado. Intenta de nuevo en unos momentos."
ERRORES_GEMINI = {
    "saturado": GEMINI_SATURADO,
    "gemini_ocupado": "Hay varias consultas a Gemini en curso. Intenta de nuevo en unos segundos.",
}
ERRORES_VISION = {
    **ERRORES_GEMINI,
    "configuracion": "🔌 Parce, falta configurar la clave o el modelo de Gemini para mirar fotos.",
    "clave": "🔌 Gemini no aceptó la clave o el acceso, parce. Toca revisar la configuración.",
    "modelo": "🎚️ Ese modelo de Gemini no salió al escenario. Toca revisar la configuración, parce.",
    "cuota": "🥁 Se llenó el pogo de fotos, parce. Espera un minuto e inténtalo de nuevo.",
    "timeout": "🎸 La foto se quedó en prueba de sonido. Reenvíala en un rato, hermano.",
    "conexion": "🔌 Se nos soltó el cable, parce. Prueba cuando vuelva la conexión.",
    "imagen": "📸 Esa imagen no entró al toque, parce. Prueba con una foto en JPEG, PNG o WebP.",
    "tamano": "📸 Esa foto pesa más que el amplificador, parce. Manda una de hasta 12 MiB.",
    "descarga": "📸 Telegram no me soltó la foto, hermano. Intenta enviarla otra vez.",
    "servicio": "🔥 Gemini anda fuera de ritmo, parce. Prueba un poco más tarde.",
}

FRASES_FOTO = {
    "alta": (
        "🤘 Parce, reconocí a {banda} en la foto.",
        "🔥 ¡Ese riff tiene nombre, hermano! En la foto aparece {banda}.",
    ),
    "media": (
        "📸 Parce, creo que es {banda}.",
        "🎸 Hermano, por las pistas de la foto creo que es {banda}.",
    ),
}

FRASES_FOTO_SIN_BANDA = (
    "📸 Parce, la foto tiene actitud, pero me faltó el logo para cerrar el riff.",
    "🎸 Hermano, la foto pasó la prueba de sonido, pero me faltaron pistas.",
)


def _descripcion_breve(descripcion):
    frase = re.split(r"(?<=[.!?])\s+|[;\n]", descripcion.strip(), maxsplit=1)[0]
    frase = re.split(
        r"\b(?:sin\s+|no\s+(?:hay|se\s+(?:ve[n]?|observa[n]?|aprecia[n]?|"
        r"distingue[n]?|identifica[n]?)|aparece[n]?|tiene[n]?)\b)",
        frase, maxsplit=1, flags=re.IGNORECASE,
    )[0]
    frase = re.sub(r"(?:[,;:]?\s*(?:\b(?:y|pero|aunque|mientras que|además)\b))?[,;:\s]+$", "", frase)
    if len(frase) > 120:
        return frase[:119].rsplit(" ", 1)[0].rstrip(",;:") + "…"
    return frase or "No se pudo describir."


def formatear_foto(resultado):
    banda = resultado["banda"]
    if banda:
        confianza = resultado["confianza"]
        respuesta = choice(FRASES_FOTO[confianza]).format(banda=banda)
        if resultado["album"]:
            prefijo = "Posible álbum" if confianza == "media" else "Álbum"
            respuesta += f" 💿 {prefijo}: {resultado['album']}."
        return respuesta

    descripcion = resultado["descripcion"]
    if not resultado["pistas"]:
        return (
            f"📸 {_descripcion_breve(descripcion)}\n"
            "Parce, no identifico personas por su cara. Escríbeme el nombre de la banda "
            "o mándame otra foto con un logo o texto visible. 🤘"
        )
    return (
        f"{choice(FRASES_FOTO_SIN_BANDA)}\n{descripcion}\n"
        "Escríbeme el nombre de la banda o mándame una foto más clara. 🤘"
    )


PEDIR_CANCION = "💿 Parce, escribe /cancion seguido del título o manda un fragmento con «busca esta letra:»."
DEMORA_CANCION = "Sigo analizando la canción. Puede tardar unos segundos más."
ERRORES_AUDIO = {
    "audio_corto": "El audio es demasiado corto. Graba al menos unos segundos de la melodía.",
    "servicio": "El servicio de reconocimiento de audio no está disponible. Intenta más tarde.",
    "audio_largo": "🎸 Ese clip se fue de gira, parce. Mándame uno de 10 a 20 segundos, de hasta 10 MB.",
    "audio_vacio": "🎙️ Llegó puro silencio, parce. Graba un pedacito y vuelve a mandarlo.",
    "formato_audio": "🔌 Ese formato no sonó, hermano. Prueba enviando el clip como MP3.",
    "imagen": "🔌 Gemini no pudo leer el audio, parce. Prueba enviándolo como MP3.",
    "falta_ffmpeg": "🔌 Gemini no aceptó el formato, parce. Envíame un MP3 o configura FFmpeg en el equipo del bot.",
    "descarga_audio": "🎙️ Telegram no me soltó el audio, parce. Intenta enviarlo de nuevo.",
    "timeout": "El reconocimiento de audio tardó demasiado. Intenta con un fragmento más corto.",
}
def formatear_audio_sin_cancion(resultado):
    texto = (
        "No pude identificar esta canción con suficiente seguridad. Intenta grabar un fragmento "
        "más largo, con menos ruido o interpretando la melodía principal."
    )
    alternativas = resultado.get("alternativas", [])
    if alternativas:
        texto += "\n\nPosibles coincidencias sin confirmar:\n" + "\n".join(
            f"• {pista['cancion']} — {pista['artista']}" for pista in alternativas[:3]
        )
    if resultado.get("servicio_limitado"):
        texto += "\n\nParte de la verificación no está disponible en este momento."
    return texto


def formatear_letra_incierta(resultado):
    respuesta = "🎧 Parce, no estoy seguro de qué tema es. Quedó sin verificar la letra."
    if resultado.get("tipo") in ("instrumental", "tarareo"):
        respuesta += "\n" + formatear_audio_sin_cancion({})
    alternativas = resultado.get("alternativas", [])
    if alternativas:
        respuesta += "\n\nPodría ser…\n" + "\n".join(
            f"🎸 {pista['cancion']} · {pista['artista']}" for pista in alternativas[:2]
        )
    return respuesta + "\n\nSi sabes el nombre de la banda, pásamelo."


def formatear_cancion(pista, confianza, en_catalogo, *, letra_verificada=None):
    if pista.get("evidencia"):
        entrada = "🎵 Canción identificada"
    elif confianza == "media" or letra_verificada is False:
        entrada = "🎧 Parce, creo que es este tema."
    elif letra_verificada:
        entrada = "🤘 La letra coincide, parce."
    elif en_catalogo:
        entrada = "🤘 Parce, este tema aparece en el catálogo."
    else:
        entrada = "🎧 Parce, Gemini sugiere este tema."
    credito = "Composición" if pista.get("rol_artista") == "compositor" else "🎸 Artista"
    respuesta = f"{entrada}\n💿 Canción: {pista['cancion'][:200]}\n{credito}: {pista['artista'][:200]}"
    if pista.get("album"):
        respuesta += f"\n📀 Álbum: {pista['album'][:200]}"
    if pista.get("evidencia") and pista.get("genero"):
        respuesta += f"\nGénero musical: {pista['genero'][:100]}"
    if letra_verificada is False:
        respuesta += "\n🔎 Resultado sin verificar la letra."
    if letra_verificada is not None and en_catalogo:
        respuesta += "\n💽 El título y el artista aparecen en el catálogo."
    elif not en_catalogo:
        respuesta += "\n💽 Resultado sin verificar en el catálogo."
    return respuesta


PEDIR_ACORDES = "🎸 Parce, escribe /acordes <banda - canción> o /acordes <canción>."
BOTON_ACORDES_VENCIDO = "Ese botón ya salió de gira, parce. Prueba con /acordes."
ERROR_ACORDES = "🎚️ Se nos cruzó un cable al buscar los acordes, parce. Intenta en un rato."
DIAGRAMA_NO_ENVIADO = "No pude enviar el diagrama. Puedes usar los acordes en texto y los enlaces del mensaje anterior."
DIAGRAMA_SIN_CONFIRMACION = (
    "Telegram no confirmó el envío del diagrama. Si no aparece, consulta los acordes y los enlaces del mensaje anterior."
)
SIN_ACORDES = "🎸 No tengo los acordes de este tema, parce."
AVISO_ACORDES = "⚠️ Acordes aproximados generados por IA: verifícalos antes de tocar"


def formatear_acordes(cancion, artista, datos):
    if not datos:
        return SIN_ACORDES
    titulo = " ".join(cancion.split())[:150]
    nombre = " ".join((artista or "").split())[:150] or "Sin identificar"
    lineas = [
        f"🎸 Canción: {titulo}",
        f"Artista: {nombre}",
        f"Tono: {datos['tonalidad'] or 'Sin identificar'}",
        f"Afinación: {datos['afinacion'] or 'Sin identificar'}",
        "",
    ]
    for seccion, acordes in datos["secciones"].items():
        lineas.append(f"{seccion.capitalize()}: {' · '.join(acordes)}")
    return "\n".join([*lineas, "", AVISO_ACORDES])
