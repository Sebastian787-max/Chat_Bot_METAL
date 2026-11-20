"""Validación reproducible con audio propio sintetizado y servicios simulados."""

import json
import os
import unittest
from collections import OrderedDict
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from subprocess import CompletedProcess
from tempfile import TemporaryDirectory
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np

import audio
import catalogo_melodias
import reconocimiento_audio as motor


_NOTAS = [60, 64, 62, 67, 65, 69, 66, 71, 68, 73, 70, 67]
_RITMO = [1, 0.65, 1, 1.4, 0.8, 1, 0.65, 1.4, 1, 0.8, 1, 1]
_OTRA = [60, 61, 65, 62, 60, 66, 64, 60, 59, 61, 69, 66]
_PISTA = {"cancion": "Melodía de prueba", "artista": "Composición sintética"}


def sintetizar(timbre, *, notas=_NOTAS, tono=0, velocidad=1, ruido=0, silencio=0, volumen=1, retumbe=0):
    rng = np.random.default_rng(912)
    segmentos = [np.zeros(int(motor.TASA_AUDIO * silencio))]
    armonicos = {
        "cantado": (1, 0.31, 0.32, 0.16),
        "tarareo": (1, 0.12, 0.05),
        "silbido": (1,),
        "guitarra": (1, 0.52, 0.31, 0.18, 0.11),
        "piano": (1, 0.47, 0.24),
        "original": (1, 0.4, 0.31, 0.18),
    }
    for nota, ritmo in zip(notas, _RITMO):
        frecuencia = 440 * 2 ** ((nota + tono - 69) / 12)
        tiempo = np.arange(round(motor.TASA_AUDIO * 0.24 * ritmo / velocidad)) / motor.TASA_AUDIO
        fase = 2 * np.pi * frecuencia * tiempo
        if timbre == "cantado":
            fase += 0.48 * np.sin(2 * np.pi * 5 * tiempo)
        onda = sum(amplitud * np.sin(numero * fase)
                   for numero, amplitud in enumerate(armonicos[timbre], 1))
        envolvente = np.minimum(1, np.minimum(tiempo, tiempo[-1] - tiempo) * 85)
        if timbre in ("guitarra", "piano"):
            envolvente *= np.exp(-tiempo * 4)
        segmentos.append(onda * envolvente * volumen)
    segmentos.append(np.zeros(int(motor.TASA_AUDIO * silencio)))
    pcm = np.concatenate(segmentos)
    pcm += rng.normal(0, ruido, pcm.shape)
    pcm += retumbe * np.sin(2 * np.pi * 14 * np.arange(len(pcm)) / motor.TASA_AUDIO)
    return motor.como_wav(np.clip(11000 * pcm, -32768, 32767).astype("<i2"))


class PruebasReconocimientoMusical(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.dict(os.environ, {}, clear=True))
        self.enterContext(patch.object(audio, "_CACHE_AUDIO", OrderedDict()))
        carpeta = Path(self.enterContext(TemporaryDirectory()))
        self.catalogo = carpeta / "melodias.json"
        self.guardar_catalogo(
            dict(_PISTA, notas=list(zip(_NOTAS, _RITMO))),
            {"cancion": "Melodía distractora", "artista": "Otra composición",
             "notas": list(zip(_OTRA, _RITMO))},
        )
        os.environ["MOSHPIT_CATALOGO_MELODIAS"] = str(self.catalogo)
        self.gemini = self.enterContext(patch.object(audio, "consultar_gemini", return_value=(
            '{"cancion":null,"artista":null,"tipo":"desconocido","confianza":"baja"}', None,
        )))

    def guardar_catalogo(self, *pistas):
        self.catalogo.write_text(json.dumps(pistas), encoding="utf-8")

    def test_catalogo_importado_reconoce_transposicion_y_cambio_de_velocidad(self):
        catalogo = self.catalogo.with_name("importado.json")
        os.environ["MOSHPIT_CATALOGO_MELODIAS"] = str(catalogo)
        original = catalogo.with_name("ensayo.wav")
        segundo = catalogo.with_name("segundo.wav")
        repetido = catalogo.with_name("repetido.wav")
        ruido = catalogo.with_name("ruido.wav")
        original.write_bytes(sintetizar("silbido"))
        segundo.write_bytes(sintetizar("guitarra", notas=_OTRA))
        repetido.write_bytes(sintetizar("silbido", tono=4, velocidad=1.16))
        ruido.write_bytes(motor.como_wav(
            np.random.default_rng(41).integers(-800, 800, size=64000, dtype=np.int16)
        ))
        comando = ["--catalogo", str(catalogo)]
        importar = comando + ["importar", "--titulo", _PISTA["cancion"], "--artista", _PISTA["artista"]]
        salida = StringIO()
        with redirect_stdout(salida), redirect_stderr(StringIO()):
            self.assertEqual(catalogo_melodias.main(importar + [str(original), str(segundo)]), 0)
            inicial = catalogo.read_bytes()
            self.assertEqual(catalogo_melodias.main(importar + [str(repetido)]), 0)
            self.assertEqual(catalogo_melodias.main(importar + [str(original), str(ruido)]), 1)
            self.assertEqual(catalogo.read_bytes(), inicial)
            self.assertEqual(catalogo_melodias.main(comando + ["listar"]), 0)
            self.assertEqual(catalogo_melodias.main(comando + ["estado"]), 0)
        self.assertIn("Duplicados omitidos: 1", salida.getvalue())
        self.assertIn("1 canciones, 2 fragmentos", salida.getvalue())
        for tono, velocidad in [(4, 1), (0, 0.8), (-3, 1.2)]:
            with self.subTest(tono=tono, velocidad=velocidad):
                consulta = sintetizar("tarareo", tono=tono, velocidad=velocidad)
                resultado = audio.analizar_audio(consulta, "audio/wav")
                self.assertEqual(resultado, dict(_PISTA, evidencia="melodia", confianza="alta"))
        self.gemini.assert_not_called()

    def test_canto_tarareo_silbido_guitarra_piano_y_bajo_sinteticos(self):
        for timbre, tono, velocidad in (
            ("cantado", 3, 1.15), ("tarareo", -5, 0.78), ("silbido", 12, 1.0),
            ("guitarra", 2, 0.92), ("piano", -2, 1.22), ("guitarra", -23, 1.0),
        ):
            with self.subTest(timbre=timbre, tono=tono):
                resultado = audio.analizar_audio(sintetizar(timbre, tono=tono, velocidad=velocidad), "audio/wav")
                self.assertEqual(resultado.get("evidencia"), "melodia", resultado)
                self.assertEqual(resultado["cancion"], _PISTA["cancion"])
        self.gemini.assert_not_called()

    def test_ruido_volumen_bajo_y_silencio_extendido(self):
        datos = sintetizar("guitarra", ruido=0.014, silencio=5, volumen=0.14, retumbe=0.12)
        pcm, silencio = motor.preparar_audio(datos)
        self.assertFalse(silencio)
        self.assertLess(len(pcm) / motor.TASA_AUDIO, 4.5)
        self.assertLessEqual(np.abs(pcm).max(), 0.85 * 32767)
        self.assertEqual(audio.analizar_audio(datos, "audio/wav").get("evidencia"), "melodia")

    def test_original_flujo_acoustid_y_cache_con_proveedor_simulado(self):
        os.environ["ACOUSTID_API_KEY"] = "clave-ficticia"
        datos = sintetizar("original", silencio=4)
        respuesta = Mock(status_code=200)
        respuesta.json.return_value = {"status": "ok", "results": [{
            "score": 0.97, "recordings": [{"title": _PISTA["cancion"], "artists": [{"name": _PISTA["artista"]}]}],
        }]}
        with patch.object(motor.shutil, "which", return_value="fpcalc"), patch.object(
            motor.subprocess, "run", return_value=CompletedProcess([], 0, b'{"duration":11,"fingerprint":"abc"}', b"")
        ) as fpcalc, patch.object(motor.requests, "post", return_value=respuesta) as servicio, patch.object(
            motor, "_PROXIMA_HUELLA", 0.0
        ):
            primera = audio.analizar_audio(datos, "audio/wav")
            primera["artista"] = "mutada"
            result = audio.analizar_audio(datos, "audio/wav")
        self.assertEqual(result, dict(_PISTA, confianza="alta", evidencia="huella"))
        fpcalc.assert_called_once()
        self.assertIs(fpcalc.call_args.kwargs["input"], datos)
        servicio.assert_called_once()
        self.assertEqual(servicio.call_args.kwargs["data"]["fingerprint"], "abc")
        self.assertNotIn(datos, servicio.call_args.kwargs["data"].values())
        self.gemini.assert_not_called()

    def test_silencio_audio_corto_y_ruido_sin_musica(self):
        with self.assertRaisesRegex(audio.ErrorVision, "audio_vacio"):
            audio.analizar_audio(motor.como_wav(np.zeros(32000, dtype="<i2")), "audio/wav")
        with self.assertRaisesRegex(audio.ErrorVision, "audio_corto"):
            audio.analizar_audio(motor.como_wav(np.ones(8000, dtype="<i2")), "audio/wav")
        self.gemini.assert_not_called()
        ruido = np.random.default_rng(41).integers(-800, 800, size=64000, dtype=np.int16)
        self.assertIsNone(audio.analizar_audio(motor.como_wav(ruido), "audio/wav")["cancion"])

    def test_melodia_ajena_al_catalogo(self):
        datos = sintetizar("silbido", notas=[53, 54, 60, 54, 53, 66, 59, 56, 57, 52, 59, 65])
        self.assertIsNone(audio.analizar_audio(datos, "audio/wav")["cancion"])

    def test_catalogo_ambiguo_solo_devuelve_candidatos(self):
        self.guardar_catalogo(
            dict(_PISTA, notas=list(zip(_NOTAS, _RITMO))),
            {"cancion": "Otra obra", "artista": "Otro artista", "notas": list(zip(_NOTAS, _RITMO))},
        )
        resultado = audio.analizar_audio(sintetizar("tarareo"), "audio/wav")
        self.assertIsNone(resultado["cancion"])
        self.assertEqual(len(resultado["alternativas"]), 2)

    def test_huella_en_conflicto_con_melodia_conserva_la_ambiguedad(self):
        conflicto = {"cancion": "Otra grabación", "artista": "Otra banda"}
        with patch.object(audio, "buscar_huella", return_value=[(0.88, conflicto)]):
            resultado = audio.analizar_audio(sintetizar("guitarra"), "audio/wav")
        self.assertIsNone(resultado["cancion"])
        self.assertEqual(resultado["alternativas"], [conflicto, _PISTA])
        self.gemini.assert_not_called()

    def test_fallo_de_huella_permite_buscar_por_melodia(self):
        os.environ["ACOUSTID_API_KEY"] = "clave-ficticia"
        with patch.object(motor.shutil, "which", return_value=None), self.assertLogs(audio.__name__):
            resultado = audio.analizar_audio(sintetizar("silbido"), "audio/wav")
        self.assertEqual(resultado.get("evidencia"), "melodia")

    def test_gemini_no_inventa_confirmacion_y_descarta_titulo_fuera_de_catalogo(self):
        os.environ.pop("MOSHPIT_CATALOGO_MELODIAS")
        self.gemini.return_value = (
            '{"cancion":"Propuesta","artista":"Artista","tipo":"tarareo","confianza":"alta"}', None,
        )
        datos = sintetizar("tarareo")
        with patch.object(audio, "buscar_cancion", return_value=None):
            self.assertEqual(audio.analizar_audio(datos, "audio/wav")["alternativas"], [])
        with patch.object(audio, "buscar_cancion", return_value=_PISTA):
            resultado = audio.analizar_audio(datos, "audio/wav")
        self.assertEqual(resultado["alternativas"], [_PISTA])
        self.assertIsNone(resultado["cancion"])
        self.assertNotIn("evidencia", resultado)


    def test_catalogo_vacio_se_distingue_de_ausente(self):
        self.guardar_catalogo()
        with self.assertLogs("audio", level="INFO") as registro:
            resultado = audio.analizar_audio(sintetizar("piano"), "audio/wav")
        diagnostico = "\n".join(registro.output)
        self.assertIn("catalogo_vacio", diagnostico)
        self.assertIn("clave_no_configurada", diagnostico)
        self.assertIn("respuesta_sin_candidato_util", diagnostico)
        self.assertIn("estado=sin_coincidencias", diagnostico)
        self.assertIsNone(resultado["cancion"])

    def test_ffmpeg_ausente_y_fallo_son_distinguibles(self):
        from subprocess import CalledProcessError

        with patch.object(motor.shutil, "which", return_value=None), self.assertLogs("audio") as registro:
            self.assertIsNone(motor.decodificar_audio(b"ogg simulado"))
        self.assertIn("ffmpeg_no_disponible", registro.output[0])
        with patch.object(motor.shutil, "which", return_value="ffmpeg"), patch.object(
            motor.subprocess, "run", side_effect=CalledProcessError(1, "ffmpeg"),
        ), self.assertLogs("audio", level="WARNING") as registro:
            self.assertIsNone(motor.decodificar_audio(b"ogg simulado"))
        self.assertIn("ffmpeg_fallo", registro.output[0])

    def test_fallo_de_catalogo_conserva_hasta_tres_propuestas_sin_confirmar(self):
        import estilos
        from lastfm import ErrorLastFM

        os.environ.pop("MOSHPIT_CATALOGO_MELODIAS")
        self.gemini.return_value = (json.dumps({
            "cancion": "Tema A", "artista": "Banda A", "confianza": "alta", "tipo": "grabacion",
            "alternativas": [{"cancion": "Tema B", "artista": "Banda B"},
                             {"cancion": "Tema C", "artista": "Banda C"}],
        }), None)
        with patch.object(audio, "buscar_cancion", side_effect=ErrorLastFM("conexion")), self.assertLogs(
            "audio", level="INFO",
        ) as registro:
            resultado = audio.analizar_audio(sintetizar("original"), "audio/wav")
        self.assertIsNone(resultado["cancion"])
        self.assertNotIn("evidencia", resultado)
        self.assertTrue(resultado["servicio_limitado"])
        self.assertEqual(len(resultado["alternativas"]), 3)
        texto = estilos.formatear_audio_sin_cancion(resultado)
        self.assertIn("Posibles coincidencias sin confirmar", texto)
        self.assertIn("Tema C", texto)
        self.assertIn("servicio_no_disponible", "\n".join(registro.output))
        for dato in ("Tema A", "Banda A"):
            self.assertNotIn(dato, "\n".join(registro.output))
