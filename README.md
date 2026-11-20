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

Ante un 503 o 429 temporal, el bot espera 2 y 4 segundos entre intentos, con un máximo de tres por modelo y dentro del presupuesto de la consulta. Si hace falta, usa el modelo alternativo configurado en `GEMINI_FALLBACK_MODEL`. Si todos los intentos disponibles devuelven 503, pausa las nuevas consultas de Gemini durante 20 segundos.

GEMINI_MAX_CONCURRENTES limita las operaciones activas, incluidos sus reintentos, a entre 1 y 8 (1 por defecto). La cola espera hasta 12 segundos y cuenta dentro del presupuesto de la operación. Los registros distinguen operación, llamada y función, con un identificador de usuario y chat seudónimo por arranque. Mientras una entrada idéntica del mismo usuario y chat se procesa para la misma función, las actualizaciones comparten el análisis y cada una recibe su propia respuesta.

## Pruebas en Telegram

La búsqueda de letras usa Google Search a través de Gemini. Solo una identificación con evidencia y metadatos de búsqueda puede llegar a confianza alta. iTunes y Last.fm sirven para comprobar que el título y el artista existen en el catálogo y para obtener la portada. Cuando la búsqueda por letra es incierta, el bot muestra hasta dos alternativas y botones para buscar en Google, Genius y Bandcamp.

`GEMINI_BUSQUEDA_WEB` es opcional. La búsqueda está activada por defecto (`si`); configúrala como `no` para desactivarla. Si la herramienta no está disponible o se agota su cuota, el bot vuelve a consultar sin búsqueda y marca el resultado como «sin verificar la letra». Usa `/cancion <título>` si sabes el título exacto y `/cancion busca esta letra: <fragmento>` para indicar explícitamente que quieres buscar por letra.

Para grabar una nota de voz en el móvil, mantén presionado el micrófono del chat y suéltalo para enviarla. Si ves una cámara, tócala para cambiar al micrófono. Prueba con 10 a 20 segundos de la pista original o canta una parte con letra. También puedes adjuntar un MP3, OGG, M4A/MP4 o WAV. El límite por audio es de 60 segundos y 10 MB.

### Reconocimiento de audio

- Envía la melodía principal durante 10 a 20 segundos. Se admiten notas de voz y adjuntos de audio de hasta 60 segundos y 10 MB. Con FFmpeg en el `PATH`, el bot decodifica formatos comprimidos en memoria. Los WAV PCM de 16 bits se procesan también sin FFmpeg. Se recorta silencio alrededor de la señal útil y se ajusta la ganancia con margen para no saturar. La selección del segmento tiene en cuenta el nivel de ruido y se atenúa el retumbe por debajo de 32 Hz cuando destaca.
- **Grabaciones originales.** Para activar la huella, instala [Chromaprint (fpcalc)](https://acoustid.org/chromaprint), agrégalo al `PATH` y configura `ACOUSTID_API_KEY` con una clave de aplicación de [AcoustID](https://acoustid.org/webservice). El servicio gratuito es para uso no comercial. El bot envía la huella y la duración, y verifica la identidad con los metadatos de MusicBrainz devueltos por AcoustID. Necesita un fragmento de al menos ocho segundos para esta ruta.
- **Canto, tarareo, silbido e instrumentos.** Para buscar por melodía, configura `MOSHPIT_CATALOGO_MELODIAS` con la ruta de un archivo JSON local. El bot compara intervalos de notas y duraciones relativas, por lo que una transposición o un cambio uniforme de velocidad pueden conservar la coincidencia. Funciona con una línea melódica dominante. La polifonía, la distorsión intensa, los cambios de ritmo dentro de la frase y la percusión sin altura definida limitan esta búsqueda.
- **Gemini.** Se conserva como análisis complementario. Las sugerencias requieren un título y artista que existan en el catálogo. Para confirmarlas por letra, el fragmento audible debe coincidir además con una letra externa. Una etiqueta de confianza de Gemini por sí sola no confirma una canción.
- Una identificación confirmada puede incluir portada, álbum, género y un enlace de escucha si el catálogo los devuelve, además del botón de acordes. Las coincidencias dudosas se muestran como candidatos. Los umbrales son heurísticos y no se presentan como porcentajes de precisión. Los resultados confirmados se guardan en memoria por 15 minutos, sin guardar el audio.

#### Catálogo local de melodías

Configura `MOSHPIT_CATALOGO_MELODIAS=catalogo/melodias.json` en `.env` y reinicia el bot para que lea la variable. Las rutas relativas de esa variable se resuelven desde la carpeta de `reconocimiento_audio.py`. También puedes usar una ruta absoluta. Para trabajar con otro catálogo desde la terminal, pasa `--catalogo ruta.json` antes del subcomando.

En la terminal PowerShell de VS Code, desde la raíz del proyecto, usa el Python del entorno virtual.

```powershell
.\venv\Scripts\python.exe .\catalogo_melodias.py estado
.\venv\Scripts\python.exe .\catalogo_melodias.py importar --titulo "Mi canción" --artista "Mi banda" --fuente "Grabación propia" --licencia "Autorizada por su autor" ".\grabaciones\coro.wav" ".\grabaciones\verso.ogg"
.\venv\Scripts\python.exe .\catalogo_melodias.py listar
```

Usa un archivo WAV, MP3, FLAC u OGG por fragmento, de hasta 30 segundos y 50 MiB. Para los formatos comprimidos, comprueba que `ffmpeg -version` funcione en esa misma terminal. Prioriza una voz aislada, un silbido o un instrumento tocado nota por nota. En una mezcla completa, el estimador puede seguir otra línea o producir una transcripción poco fiable. Si la herramienta rechaza un fragmento, vuelve a grabarlo de forma aislada con notas claras y estables. Un archivo rechazado cancela la importación del lote.

El importador compara títulos y artistas normalizados y omite fragmentos de la misma canción con los mismos intervalos y un ritmo equivalente. Usa `--fuente` y `--licencia` para documentar el origen y permiso de las grabaciones y conservar la atribución exigida. Las letras, los arreglos y las grabaciones pueden tener derechos distintos a los de la composición.

El JSON almacena una entrada por fragmento. Cada par contiene una nota MIDI y su duración relativa al valor mediano de la frase. El motor obtiene los intervalos al restar las alturas de notas consecutivas. También puedes agregar transcripciones autorizadas en este formato.

```json
[
  {
    "cancion": "Melodía propia de ejemplo",
    "artista": "Autor del catálogo",
    "notas": [[60, 1], [64, 0.75], [62, 1], [67, 1.5], [65, 1], [69, 1], [66, 0.75], [71, 1]]
  }
]
```

El índice admite hasta 500 entradas y de 8 a 128 notas por entrada. Puedes añadir varios fragmentos de la misma canción con el mismo título y artista. No se incluye un catálogo de canciones comerciales.

Para diagnosticar una nota de voz, busca en el registro las líneas Audio op= con el mismo ID que Gemini. El diagnóstico indica si se decodificó el audio, si se consultaron la huella y el catálogo melódico, si hubo una propuesta y cómo terminó la verificación. Los estados se registran sin guardar el título sugerido, el artista, la letra ni el contenido del audio.

Las pruebas automatizadas generan WAV sintéticos para probar la extracción y la comparación local. Las respuestas de los proveedores se simulan. Para medir precisión en la práctica, prueba grabaciones originales autorizadas y distintas tomas humanas, tanto de temas indexados como de temas ajenos al catálogo.

#### Sincronización de partituras autorizadas

Ejecuta estos comandos en PowerShell desde la raíz. El sincronizador corre fuera del proceso de Telegram y reutiliza el mismo JSON. Tras desplegar el código nuevo, basta con que el proceso del bot esté ejecutándose con la ruta configurada. Cada cambio del archivo invalida la caché del catálogo y la de resultados de audio.

```powershell
.\venv\Scripts\python.exe -m pip install "mido==1.3.3" "defusedxml==0.7.1"
.\venv\Scripts\python.exe .\sincronizador_catalogo.py --dry-run
.\venv\Scripts\python.exe .\sincronizador_catalogo.py --una-vez
.\venv\Scripts\python.exe .\catalogo_melodias.py sincronizacion
.\venv\Scripts\python.exe .\catalogo_melodias.py listar
```

`--dry-run` descarga y valida con los límites reales y muestra los resultados sin escribir el catálogo ni su registro. `--una-vez` intenta sincronizar de inmediato. `--si-pendiente` aplica el intervalo configurado. `--estado` muestra la misma actividad que el subcomando `sincronizacion`. Usa `--catalogo` y `--config` para elegir rutas alternativas.

La configuración está en [catalogo/sincronizacion.json](catalogo/sincronizacion.json). Por defecto se habilita OpenScore y se evalúan hasta cuatro archivos cada 24 horas. `prioridades` ordena las categorías declaradas de las fuentes habilitadas. `max_bytes_catalogo` limita el JSON, además del máximo de 500 fragmentos del motor. El registro local `melodias.json.sync.json` conserva los últimos 200 eventos, incluidos rechazos, errores de red, nuevas obras y aplazamientos. Los binarios descargados se procesan en memoria. Las escrituras usan un archivo temporal y reemplazo atómico. Los bloqueos del sistema operativo se liberan cuando termina el proceso, incluso ante un cierre inesperado. En caso de interrupción, las obras ya publicadas se detectan por su identificador de origen en el propio catálogo. Un fallo temporal permite volver a intentar en la siguiente comprobación, al cabo de al menos una hora.

**En Windows.** Abre el Programador de tareas y crea una tarea para tu cuenta. En **Desencadenadores**, selecciona una programación y repítela cada hora indefinidamente. En **Acciones**, selecciona **Iniciar un programa** e indica los siguientes valores, ajustando la ruta si mueves el proyecto.

| Campo | Valor |
| --- | --- |
| Programa | `C:\CHATBOT\machine-learning-Bot-telegram\venv\Scripts\python.exe` |
| Argumentos | `-X utf8 "C:\CHATBOT\machine-learning-Bot-telegram\sincronizador_catalogo.py" --si-pendiente` |
| Iniciar en | `C:\CHATBOT\machine-learning-Bot-telegram` |

En **Configuración** elige **Ejecutar la tarea lo antes posible si se omitió un inicio programado** y **No iniciar una instancia nueva** si la tarea ya está en ejecución. Ejecuta la tarea desde el Programador para verificarla. Si aún no vence el intervalo, el resultado esperado es una salida correcta sin sincronización. Revisa el historial de la tarea y el subcomando `sincronizacion` para ver el último resultado. [Documentación del Programador de tareas](https://learn.microsoft.com/en-us/powershell/module/scheduledtasks/new-scheduledtaskaction?view=windowsserver2025-ps)

**Añadir repertorio de un sello, banda o autor.** Habilita `"manifiesto"` en `fuentes` y configura la propiedad `manifiesto` con una ruta relativa al archivo de configuración. Usa un índice local mantenido por quien publica el repertorio o por quien haya revisado los permisos. Confirma antes de publicar el índice que los derechos sobre la composición y la edición o transcripción concreta cubren las licencias declaradas. El sincronizador consulta de nuevo ese índice en cada ejecución y comprueba el SHA-256 de cada archivo. Las rutas locales deben estar dentro de la carpeta del manifiesto. Para descargas, enumera los dominios exactos en `hosts_manifiesto` y usa enlaces HTTPS directos sin credenciales, parámetros ni redirecciones. Este conector acepta [CC0 1.0](https://creativecommons.org/publicdomain/zero/1.0/) y [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). CC BY 4.0 permite uso comercial y adaptaciones con atribución, enlace a la licencia e indicación de cambios. Conserva estos datos al compartir el catálogo. La declaración del manifiesto depende de la revisión del operador.

```json
[
  {
    "id": "obra-001",
    "archivo": "partituras/obra-001.mid",
    "sha256": "<64 caracteres hexadecimales en minúscula>",
    "titulo": "Título publicado",
    "compositor": "Nombre acreditado",
    "categoria": "metal",
    "url_original": "https://example.org/obras/obra-001",
    "licencia_url": "https://creativecommons.org/licenses/by/4.0/",
    "evidencia": "https://example.org/obras/obra-001/licencia",
    "atribucion": "Crédito exigido por el autor o editor",
    "composicion_y_edicion_autorizadas": true
  }
]
```

Obtén el digest con `(Get-FileHash .\partituras\obra-001.mid -Algorithm SHA256).Hash.ToLowerInvariant()` desde la carpeta del manifiesto. El resultado importa hasta tres frases de 8 a 17 notas para cada partitura. Cada entrada conserva origen, URL fijada a la revisión cuando está disponible, fecha, licencia, autoría y digest. La adaptación consiste en extraer fragmentos, convertir las alturas a MIDI y normalizar las duraciones. El compositor se muestra como tal en Telegram. Las sincronizaciones agregan obras o frases nuevas. Una obra ya incorporada se mantiene en el catálogo y requiere revisión explícita si cambia su permiso o si hay que sustituir su edición.

##### Evaluación de fuentes musicales

| Fuente | Acceso, licencia y decisión |
| --- | --- |
| MusicBrainz | API documentada de metadatos y entidades. Datos básicos CC0 y suplementarios CC BY-NC-SA 3.0. La API gratuita se ofrece para uso no comercial. Sin secuencias melódicas. [API](https://musicbrainz.org/doc/MusicBrainz_API) · [Licencias](https://musicbrainz.org/doc/About/Data_License) |
| AcoustID | API de huellas e identificación, hasta tres solicitudes por segundo y acceso gratuito no comercial. Las huellas no representan alturas ni duraciones musicales. Sigue en el flujo actual de reconocimiento. [Servicio](https://acoustid.org/webservice) |
| Wikimedia Commons | API documentada y fichas por archivo. Hay audio y MIDI con permisos variados y condiciones de atribución o de jurisdicción. La edición o grabación y la composición requieren revisión. Se puede usar un elemento revisado mediante el manifiesto. [Reutilización](https://commons.wikimedia.org/wiki/Commons:Reusing_content_outside_Wikimedia) · [API de archivos](https://www.mediawiki.org/wiki/API:Imageinfo/en) |
| Internet Archive | API documentada para leer archivos y metadatos por ítem. El acceso público y las declaraciones del depositante requieren confirmar los derechos concretos de grabación y obra. Los términos vigentes enlazados solicitan JavaScript y su texto completo quedó sin verificar en este entorno. Sin conector automático. [API](https://archive.org/developers/md-read.html) · [Términos](https://archive.org/about/terms) |
| MuseScore.com | Sus términos limitan las descargas y restringen los sistemas automatizados. Se usa la distribución directa e independiente que OpenScore documenta para su edición CC0. [Términos](https://musescore.com/legal/terms) |
| OpenScore Lieder | Conector habilitado. MusicXML descargable por obra desde el repositorio público. Se fija el commit, se comprueba la licencia CC0 del repositorio y se exige la declaración CC0 dentro del MusicXML. El índice oficial de compositores limita la cosecha automática a autores fallecidos en 1925 o antes. La licencia de la edición cubre los derechos que el editor puede ceder. La protección de la composición puede variar según la jurisdicción. [Catálogo oficial](https://fourscoreandmore.org/openscore/lieder/) · [Repositorio y licencia](https://github.com/OpenScore/Lieder) · [Índice de compositores](https://github.com/OpenScore/Lieder/blob/main/data/composers.yaml) · [API de GitHub](https://docs.github.com/en/rest/repos/contents) |
| Mutopia Project | MIDI y partituras con licencia por obra. El catálogo mezcla dominio público, CC BY y CC BY-SA. Las licencias BY y BY-SA permiten adaptaciones y uso comercial con atribución, y BY-SA exige mantener la licencia al distribuir la adaptación. El manifiesto admite una edición acreditada bajo CC BY 4.0 o CC0. [Licencias](https://www.mutopiaproject.org/legal.html) · [Repositorio](https://github.com/MutopiaProject/MutopiaProject) |
| Lakh MIDI | Descarga disponible con licencia CC BY 4.0 declarada para el conjunto. El autor documenta la recopilación web y las carencias de atribución individual. Las composiciones subyacentes pueden seguir protegidas. Sin conector automático. [Dataset y procedencia](https://colinraffel.com/projects/lmd/) |
| MAESTRO | MIDI y audio de piano bajo CC BY-NC-SA 4.0. La restricción comercial y la polifonía lo dejan fuera de esta integración. [Dataset y licencia](https://magenta.tensorflow.org/datasets/maestro#license) |
| The Session | Colección de melodías en ABC con condiciones específicas de licencia para el contenido y la base. Su integración requeriría comprobar los permisos por obra y añadir un lector ABC. [Licencia del repositorio](https://github.com/adactio/TheSession-data/blob/main/LICENSE.md) |

Se eligió Mido (MIT) para leer mensajes, canales y tempo con poco peso. pretty_midi (MIT) también representa instrumentos y notas. music21 (BSD de tres cláusulas) ofrece un modelo de análisis más amplio. El lector MusicXML admite un subconjunto acotado con defusedxml (PSF) y `zipfile`. Procesa los documentos como datos con las entidades deshabilitadas y lee el ZIP en memoria. [Mido](https://github.com/mido/mido/) · [pretty_midi](https://github.com/craffel/pretty-midi) · [Licencia de music21](https://raw.githubusercontent.com/cuthbertLab/music21/master/LICENSE) · [MusicXML](https://www.w3.org/2021/06/musicxml40/tutorial/compressed-mxl-files/) · [defusedxml](https://github.com/tiran/defusedxml)

##### Alcance del reconocimiento

Se admiten MIDI de tipo 0/1 y MusicXML por partes, incluido MXL. El límite por partitura es de 2 MiB en descarga, 6 MiB tras descomprimir un MXL y diez minutos de duración. Se prioriza una voz indicada por el nombre o la presencia de letra. Si no hay indicios, se exige una única línea monofónica utilizable. Se omite la percusión MIDI del canal 10. Se respetan cambios explícitos de tempo MIDI y las marcas numéricas de tempo compatibles de MusicXML. Se prescinde de adornos breves, saltos de repetición y efectos de interpretación como pedal, bend o vibrato escrito. Antes de guardar, se comprueba que el reconocedor acústico alcance su umbral para dos versiones sintéticas de la frase con tonos y velocidades diferentes. La prueba sintética mide la compatibilidad con el motor. La precisión con voces e instrumentos humanos depende de la articulación, la duración de la consulta y la similitud con otras obras. Para rock y metal comercial, hacen falta partituras o transcripciones autorizadas por obra.


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
