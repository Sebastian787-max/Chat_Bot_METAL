"""Diagnóstico manual de Gemini con una solicitud de texto."""

import os
from pathlib import Path

from dotenv import load_dotenv
from google import genai
from google.genai import types

from vision import redactar_secretos


def main():
    try:
        load_dotenv(Path(__file__).with_name(".env"))
        clave = os.getenv("GEMINI_API_KEY")
        modelo = os.getenv("GEMINI_MODEL")
        if not clave or not modelo:
            raise ValueError("Configura GEMINI_API_KEY y GEMINI_MODEL.")

        with genai.Client(
            api_key=clave,
            vertexai=False,
            http_options=types.HttpOptions(
                timeout=20_000, retry_options=types.HttpRetryOptions(attempts=1)
            ),
        ) as cliente:
            respuesta = cliente.models.generate_content(model=modelo, contents="Responde solo: ok")
            print(redactar_secretos(respuesta.text or "(respuesta sin texto)"))
    except Exception as error:
        print(redactar_secretos(f"{type(error).__name__}: {error}"))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
