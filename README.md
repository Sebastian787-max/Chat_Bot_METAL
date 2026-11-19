# Moshpit Bot 🤘

Moshpit Bot es un bot de Telegram en español colombiano. Usa pysentimiento para analizar emociones y discurso de odio. Sugiere bandas de rock y metal según el ánimo y consulta Last.fm para encontrar artistas y recomendaciones.

También identifica canciones a partir de audios o fragmentos de letra. Busca el título, el artista, el álbum y la portada en iTunes y Last.fm. La respuesta no reproduce letras.

También usa Gemini para buscar pistas de bandas y álbumes en portadas, camisetas y pósters. Puedes mandar una foto o un documento JPEG, PNG o WebP de hasta 12 MiB. La imagen y el pie de foto se envían a Gemini para el análisis.

## Preparación en Windows

Desde la carpeta del proyecto, instala las dependencias en el entorno virtual existente y arranca el bot:

```powershell
.\venv\Scripts\python.exe -m pip install -r requirements.txt
.\venv\Scripts\python.exe bot.py
```

Antes de arrancar, comprueba que tu archivo local `.env` tenga las variables `TELEGRAM_TOKEN` y `LASTFM_API_KEY`. La primera se obtiene con BotFather y la segunda con una cuenta de Last.fm. Usa `.env.example` como referencia. La primera carga de pysentimiento puede necesitar internet para descargar los modelos.

Para activar las fotos, configura también `GEMINI_API_KEY` con tu clave de Google AI Studio y `GEMINI_MODEL` con el identificador de un modelo disponible en tu cuenta que admita imágenes y respuestas JSON. El bot admite hasta cinco análisis de fotos por minuto. Cada análisis puede hacer varios intentos contra Gemini.

Si Gemini devuelve un error 503 o 429, el bot reintenta después de 2, 5 y 10 segundos. Puedes configurar `GEMINI_FALLBACK_MODEL` con otro modelo compatible. Si se agotan los intentos del principal, el bot prueba el de respaldo con las mismas esperas.

## Pruebas en Telegram

La búsqueda de letras usa Google Search a través de Gemini. Solo una identificación con evidencia y metadatos de búsqueda puede llegar a confianza alta. iTunes y Last.fm sirven para comprobar que el título y el artista existen en el catálogo y para obtener la portada. Cuando la búsqueda por letra es incierta, el bot muestra hasta dos alternativas y botones para buscar en Google, Genius y Bandcamp.

`GEMINI_BUSQUEDA_WEB` es opcional. La búsqueda está activada por defecto (`si`); configúrala como `no` para desactivarla. Si la herramienta no está disponible o se agota su cuota, el bot vuelve a consultar sin búsqueda y marca el resultado como «sin verificar la letra». Usa `/cancion <título>` si sabes el título exacto y `/cancion busca esta letra: <fragmento>` para indicar explícitamente que quieres buscar por letra.

Para grabar una nota de voz en el móvil, mantén presionado el micrófono del chat y suéltalo para enviarla. Si ves una cámara, tócala para cambiar al micrófono. Prueba con 10 a 20 segundos de la pista original o canta una parte con letra. También puedes adjuntar un MP3, OGG, M4A/MP4 o WAV. El límite por audio es de 60 segundos y 10 MB.

El bot envía el audio o el fragmento escrito a Gemini. Para identificar audio, los modelos configurados deben admitir esa modalidad. Las notas de voz se envían como OGG. Si Gemini rechaza el formato, el bot intenta convertirlo a MP3 en memoria con FFmpeg. Para habilitar esa conversión en Windows, descarga una compilación de FFmpeg y agrega su carpeta `bin` al `PATH` de la sesión del bot. También puedes enviar un MP3 directamente.

| Envía | Respuesta esperada |
| --- | --- |
| `/start` | Bienvenida y comandos |
| `/banda Black Sabbath` | Etiquetas, descripción y hasta cinco artistas similares |
| `/similares Metallica` | Lista de artistas parecidos |
| `/recomendar` | Una banda de rock o metal y un dato curioso |
| `/cancion Enter Sandman` | Busca el título en el catálogo y muestra la portada si está disponible |
| `busca esta letra: <fragmento>` o `/cancion busca esta letra: <fragmento>` | Busca evidencia de la coincidencia y muestra la ficha si identifica la canción |
| Nota de voz de una canción conocida | Intenta identificarla y muestra canción, artista, álbum y portada cuando estén disponibles |
| `/recomendar quiero saber de nirvana`, `quiero saber de nirvana` o `háblame de Slayer` | Ficha de la banda y artistas similares |
| `/recomendar black metal` | Tanda de black metal con canciones para empezar |
| `/recomendar algo parecido a Metallica` | Artistas similares a Metallica |
| `/recomendar xyzabc123` | Invitación a revisar el nombre o consultar `/generos` |
| `/generos` | Lista de géneros disponibles |
| `/genero black metal` o `algo de black metal` | Hasta cinco bandas de black metal y una canción para empezar por banda, si está disponible |
| `/banda quiero conocer algo de black metal, que me recomiendas?` | Recomendaciones de black metal |
| `recomiéndame thrash` | Recomendaciones de thrash metal |
| `algo parecido a Metallica` | Lista de artistas parecidos, igual que con `/similares Metallica` |
| `otras` justo después de una tanda | Otra tanda del mismo género, sin repetir las bandas ya mostradas en ese chat |
| `qué escucho?` | Pregunta por el género y muestra ejemplos |
| `Metallica` | La ficha de la banda, igual que con `/banda` |
| `Estoy muy triste y tengo ganas de llorar` | Análisis y sugerencias según la emoción detectada |
| `/banda Banda Inventada Xyz 123` | Mensaje que invita a revisar el nombre |
| Foto de una portada con el nombre de la banda visible | Banda y álbum si se reconocen, seguidos de la ficha y las recomendaciones de Last.fm |
| La misma portada enviada como documento PNG con pie de foto | Reconocimiento usando también el pie de foto como pista secundaria |
| Foto de una persona sin texto, logo ni otras pistas | Una descripción breve de lo visible y solicitud del nombre o de otra foto con pistas |

Prueba también `/banda` y `/similares` sin argumentos para ver los ejemplos de uso. El bot elige cada tanda al azar entre los 30 artistas más populares de la etiqueta en Last.fm. El historial de bandas mostradas se guarda por usuario y conversación en memoria hasta que se reinicia el bot. Si el texto libre no pide recomendaciones, no contiene un género ni coincide con una banda, pasa al análisis de emociones.

## Archivos

- `bot.py` registra los comandos y dirige los mensajes y las fotos al análisis correspondiente.
- `predict.py` conserva el análisis de emociones, toxicidad, intención, ghosting y riesgo.
- `lastfm.py` consulta Last.fm y maneja fallos de la API.
- `generos.py` reconoce géneros, sinónimos e intenciones de recomendación.
- `estilos.py` contiene las frases, los emojis y las recomendaciones.
- `vision.py` analiza fotos en memoria con Gemini y valida el resultado.
- `audio.py` valida audios e identifica canciones a partir de audio o letra escrita.
- `portadas.py` verifica canciones y busca portadas en iTunes y Last.fm.
