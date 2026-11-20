# predict.py - Versión para pysentimiento 0.7.3
import logging
from functools import lru_cache

# ─── Modelos preentrenados ─────────────────────────

@lru_cache(maxsize=1)
def cargar_modelos():
    from pysentimiento import create_analyzer

    # pysentimiento cambia Transformers a ERROR al importarse.
    logging.getLogger("transformers").setLevel(logging.WARNING)
    return (
        create_analyzer(task="emotion", lang="es"),
        create_analyzer(task="hate_speech", lang="es"),
    )

# ─── Intención (reglas mejoradas) ─────────────────────

def detectar_intencion(texto):
    texto = texto.lower()
    
    # Afecto positivo
    if any(palabra in texto for palabra in ["te quiero", "te amo", "me gustas", "te extraño", "te necesito", "te adoro"]):
        return "afecto"
    
    # Reclamos
    elif any(palabra in texto for palabra in ["por qué no respondes", "me ignoras", "no contestas", "por qué tardas", "por qué demoras", "no me escribes"]):
        return "reclamo"
    
    # Celos
    elif any(palabra in texto for palabra in ["con quién", "quién es", "te veo con", "andabas con", "quién te escribió", "quién te habla"]):
        return "celos"
    
    # Agresión
    elif any(palabra in texto for palabra in ["odio", "mal", "estúpido", "idiota", "imbécil", "estúpida", "mierda", "qué asco"]):
        return "agresion"
    
    # Ruptura
    elif any(palabra in texto for palabra in ["terminar", "dejar", "no quiero seguir", "acabó", "se acabó", "hasta aquí", "se terminó"]):
        return "ruptura"
    
    # Manipulación
    elif any(palabra in texto for palabra in ["si me quisieras", "deberías", "nunca haces", "siempre igual", "como siempre", "si realmente me quisieras"]):
        return "manipulacion"
    
    # Control
    elif any(palabra in texto for palabra in ["dónde estás", "qué haces", "dime la verdad", "ubicación", "envía ubicación"]):
        return "control"
    
    # Desinterés
    elif any(palabra in texto for palabra in ["ok", "vale", "ah", "ya", "como digas", "da igual", "me da igual", "jaja", "jeje", "ok ok"]):
        return "desinteres"
    
    # Curiosidad
    elif any(palabra in texto for palabra in ["qué tal", "cómo estás", "cómo te va", "en qué andas", "qué cuentas"]):
        return "curiosidad"
    
    # Tristeza
    elif any(palabra in texto for palabra in ["triste", "deprimido", "solo", "soledad", "llorar"]):
        return "tristeza"
    
    else:
        return "normal"

# ─── Ghosting ─────────────────────────────────────────

def calcular_ghosting(texto, intencion, emocion):
    score = 0
    texto = texto.lower()
    
    # Mensajes muy cortos
    if len(texto) <= 5:
        score += 35
    
    # Respuesta de una palabra
    if len(texto.split()) == 1:
        score += 25
    
    # Palabras que indican desinterés
    if any(palabra in texto for palabra in ["ok", "vale", "ah", "ya", "luego", "ocupado", "ahora no", "después", "mañana", "ahora no puedo"]):
        score += 30
    
    # Intención relacionada
    if intencion == "desinteres":
        score += 35
    elif intencion == "reclamo":
        score += 15
    elif intencion == "afecto":
        score -= 25
    
    # Emoción negativa
    if emocion in ["sadness", "anger", "fear", "disgust"]:
        score += 10
    elif emocion in ["joy", "surprise"]:
        score -= 15
    
    return max(0, min(score, 95))

# ─── Calcular riesgo ───────────────────────────────────

def calcular_riesgo(hate_result, intencion, emocion):
    # Verificar si hay hate_speech
    if hate_result.output == "hateful" or hate_result.output == "targeted":
        return "🔴 Alto"
    elif intencion in ["celos", "reclamo", "ruptura", "manipulacion", "agresion"]:
        return "🟡 Medio"
    elif emocion in ["anger", "disgust"]:
        return "🟡 Medio"
    else:
        return "🟢 Bajo"

# ─── Mapear emociones ───────────────────────────────────

def mapear_emocion(emocion_raw):
    emocion_map = {
        'joy': 'positivo',
        'sadness': 'tristeza',
        'anger': 'enojo',
        'surprise': 'sorpresa',
        'disgust': 'disgusto',
        'fear': 'miedo',
        'neutral': 'neutral'
    }
    return emocion_map.get(emocion_raw, 'neutral')

# ─── Función principal ─────────────────────────────────

def analizar_texto(texto):
    emotion_analyzer, hate_analyzer = cargar_modelos()
    try:
        # 🔥 MODELOS IA con pysentimiento
        emocion_pred = emotion_analyzer.predict(texto)
        hate_pred = hate_analyzer.predict(texto)
        
        # Obtener emoción principal
        emocion_raw = emocion_pred.output
        emocion = mapear_emocion(emocion_raw)
        
        # Toxicidad (basada en hate_speech)
        toxicidad = "si" if hate_pred.output in ["hateful", "targeted"] else "no"
        
        # Intención (reglas)
        intencion = detectar_intencion(texto)
        
        # Ghosting
        ghosting = calcular_ghosting(texto, intencion, emocion_raw)
        
        # Riesgo
        riesgo = calcular_riesgo(hate_pred, intencion, emocion_raw)
        
        # Confianza
        confianza = max(emocion_pred.probas.values())
        
        return emocion, toxicidad, ghosting, riesgo, intencion, int(confianza * 100)
    
    except Exception as e:
        print(f"Error en analizar_texto: {e}")
        return "neutral", "no", 0, "🟢 Bajo", "normal", 50
