"""Amplía el catálogo desde partituras autorizadas, fuera del proceso de Telegram."""

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

from catalogo_melodias import (
    MAX_CATALOGO, bloquear, cargar_o_crear, clave_cancion, escribir_json, guardar_catalogo,
    mismo_fragmento, resolver_catalogo,
)
from fuentes_melodicas import (
    LICENCIAS, ArchivoRetirado, Descargas, ErrorFuente, descargar, leer_acotado, manifiesto, openscore,
)
from generos import normalizar
from procesador_midi import extraer_partitura
from reconocimiento_audio import _identidad
from validador_melodias import validar_reconocible


CONFIGURACION = Path(__file__).resolve().parent / "catalogo" / "sincronizacion.json"
LIMITE_REGISTRO = 1_000_000
MAX_EVENTOS = 200
MAX_PROCESADOS = 3200


def ahora():
    return datetime.now(timezone.utc)


def configuracion(ruta):
    datos = json.loads(leer_acotado(ruta, 32_768))
    if not isinstance(datos, dict):
        raise ValueError("configuración de sincronización inválida")
    for campo, minimo, maximo in (
        ("intervalo_horas", 1, 720), ("max_archivos", 1, 30),
        ("max_bytes_catalogo", 1000, MAX_CATALOGO),
    ):
        valor = datos.get(campo)
        if type(valor) is not int or not minimo <= valor <= maximo:
            raise ValueError(f"{campo} debe estar entre {minimo} y {maximo}")
    for campo in ("fuentes", "prioridades", "hosts_manifiesto"):
        valores = datos.get(campo, [])
        if not isinstance(valores, list) or any(not isinstance(v, str) or len(v) > 100 for v in valores):
            raise ValueError(f"{campo} debe ser una lista de textos")
        datos[campo] = valores
    if set(datos["fuentes"]) - {"openscore", "manifiesto"}:
        raise ValueError("fuente de sincronización desconocida")
    if not datos["fuentes"]:
        raise ValueError("habilita al menos una fuente")
    if "manifiesto" in datos["fuentes"]:
        archivo = datos.get("manifiesto")
        if not isinstance(archivo, str) or not archivo:
            raise ValueError("indica la ruta al manifiesto autorizado")
        datos["manifiesto"] = (ruta.parent / archivo).resolve()
    return datos


def ruta_registro(ruta):
    return ruta.with_name(ruta.name + ".sync.json")


def cargar_registro(ruta):
    try:
        registro = json.loads(leer_acotado(ruta_registro(ruta), LIMITE_REGISTRO))
    except FileNotFoundError:
        return {"ultima": None, "ultimo_exito": None, "eventos": [], "procesados": {}}
    if (
        not isinstance(registro, dict) or not isinstance(registro.get("eventos"), list)
        or not isinstance(registro.get("procesados"), dict)
        or any(
            registro.get(campo) is not None and not isinstance(registro[campo], str)
            for campo in ("ultima", "ultimo_exito")
        )
    ):
        raise ValueError("registro de sincronización inválido")
    return registro


def preparar_obra(elemento, datos, huella):
    partitura = extraer_partitura(datos, elemento.extension)
    if elemento.fuente == "openscore" and set(partitura.derechos) != {"OpenScore (CC0)"}:
        raise ValueError("declaración CC0 de OpenScore ausente o contradictoria en la partitura")
    identidad = _identidad(elemento.titulo or partitura.titulo, elemento.compositor or partitura.compositor)
    if not identidad or not all(clave_cancion(identidad)):
        raise ValueError("la partitura carece de título o compositor documentado")
    fragmentos = []
    for notas in partitura.fragmentos:
        if validar_reconocible(notas):
            fragmentos.append(notas)
        if len(fragmentos) == 3:
            break
    if not fragmentos:
        raise ValueError("las frases no alcanzan el umbral acústico de reconocimiento")
    return identidad, fragmentos, {
        "fuente": elemento.fuente,
        "id_origen": elemento.clave,
        "revision_origen": elemento.revision,
        "url_origen": elemento.url_original,
        "licencia": LICENCIAS[elemento.licencia_url],
        "url_licencia": elemento.licencia_url,
        "evidencia_licencia": elemento.evidencia,
        "atribucion": elemento.atribucion,
        "categoria": elemento.categoria,
        "rol_artista": "compositor",
        "adaptacion": "Extracción de frases, conversión de alturas a MIDI y normalización de duraciones",
        "incorporado_en": ahora().isoformat(timespec="seconds"),
        "sha256_origen": huella,
    }


def incorporar(catalogo, obra):
    identidad, fragmentos, origen = obra
    clave = clave_cancion(identidad)
    nueva = []
    for notas in fragmentos:
        if any(mismo_fragmento(notas, pista["notas"]) for pista in (*catalogo, *nueva)):
            continue
        nueva.append(dict(identidad, notas=notas, **origen))
    es_nueva = bool(nueva) and all(clave_cancion(pista) != clave for pista in catalogo)
    return nueva, es_nueva


def sincronizar(ruta, cfg, *, simular=False, si_pendiente=False):
    if simular:
        return ejecutar(ruta, cfg, cargar_registro(ruta), simular=True)
    with bloquear(ruta.with_suffix(ruta.suffix + ".sync.lock")):
        registro = cargar_registro(ruta)
        ultima = registro.get("ultima")
        intervalo = cfg["intervalo_horas"] if ultima == registro.get("ultimo_exito") else 1
        if si_pendiente and ultima and ahora() < datetime.fromisoformat(ultima) + timedelta(hours=intervalo):
            print("Sincronización aún no programada para este momento.")
            return 0
        return ejecutar(ruta, cfg, registro)


def ejecutar(ruta, cfg, registro, *, simular=False):
    catalogo = cargar_o_crear(ruta)
    elementos, eventos, obras = [], [], []
    procesados = registro["procesados"]
    ids = {p.get("id_origen") for p in catalogo}
    huellas = {p.get("sha256_origen") for p in catalogo}
    prioridades = [normalizar(p) for p in cfg["prioridades"]]
    instante = ahora().isoformat(timespec="seconds")
    hubo_fallo = False
    intentados = 0

    def evento(id_origen, estado, detalle):
        detalle = "".join(c for c in detalle if c.isprintable())[:240]
        eventos.append({"fecha": instante, "id": id_origen, "estado": estado, "detalle": detalle})

    def marcar(elemento):
        procesados.pop(elemento.clave, None)
        procesados[elemento.clave] = elemento.revision

    with requests.Session() as sesion:
        sesion.trust_env = False
        sesion.headers["User-Agent"] = "MoshpitBot-Catalogo/1.0"
        red = Descargas(sesion)
        for fuente in dict.fromkeys(cfg["fuentes"]):
            try:
                elementos.extend(openscore(red) if fuente == "openscore" else manifiesto(cfg["manifiesto"]))
            except (ErrorFuente, OSError, ValueError, KeyError, TypeError, AttributeError, RecursionError) as error:
                hubo_fallo = True
                detalle = str(error) if isinstance(error, ErrorFuente) else "índice inválido o no disponible"
                evento(fuente, "error_fuente", detalle)

        def prioridad(elemento):
            categoria = normalizar(elemento.categoria)
            return prioridades.index(categoria) if categoria in prioridades else len(prioridades)

        for elemento in sorted(elementos, key=prioridad):
            if intentados >= cfg["max_archivos"] or len(catalogo) >= 500:
                break
            if elemento.clave in ids or procesados.get(elemento.clave) == elemento.revision:
                continue
            ids.add(elemento.clave)
            intentados += 1
            try:
                datos, huella = descargar(elemento, red, cfg.get("manifiesto"), set(cfg["hosts_manifiesto"]))
                if huella in huellas:
                    evento(elemento.clave, "duplicado", "archivo ya catalogado")
                    marcar(elemento)
                    continue
                obra = preparar_obra(elemento, datos, huella)
                obras.append((elemento, obra))
                huellas.add(huella)
            except ArchivoRetirado as error:
                evento(elemento.clave, "rechazado", str(error))
                marcar(elemento)
            except (ErrorFuente, OSError) as error:
                hubo_fallo = True
                detalle = str(error) if isinstance(error, ErrorFuente) else "archivo local inaccesible"
                evento(elemento.clave, "error_archivo", detalle)
            except (ValueError, RecursionError) as error:
                evento(elemento.clave, "rechazado", str(error)[:240])
                marcar(elemento)

    def aplicar(actual):
        total, composiciones = 0, 0
        for elemento, obra in obras:
            if any(p.get("id_origen") == elemento.clave for p in actual):
                continue
            nuevas, es_nueva = incorporar(actual, obra)
            if len(actual) + len(nuevas) > 500:
                evento(elemento.clave, "aplazado", "capacidad de 500 fragmentos")
                continue
            if nuevas:
                if not simular:
                    try:
                        guardar_catalogo(ruta, actual + nuevas, cfg["max_bytes_catalogo"])
                    except ValueError:
                        evento(elemento.clave, "aplazado", "límite de almacenamiento del catálogo")
                        continue
                actual.extend(nuevas)
                total += len(nuevas)
                composiciones += es_nueva
                evento(elemento.clave, "incorporable" if simular else "incorporado",
                       f"{len(nuevas)} fragmentos · {obra[0]['cancion']}")
            else:
                evento(elemento.clave, "duplicado", "fragmentos ya catalogados")
                marcar(elemento)
        return total, composiciones

    if simular:
        total, composiciones = aplicar(catalogo)
    else:
        with bloquear(ruta.with_suffix(ruta.suffix + ".lock")):
            total, composiciones = aplicar(cargar_o_crear(ruta))
        registro["ultima"] = instante
        if not hubo_fallo:
            registro["ultimo_exito"] = instante
        registro["eventos"] = (registro["eventos"] + eventos)[-MAX_EVENTOS:]
        registro["procesados"] = dict(list(procesados.items())[-MAX_PROCESADOS:])
        escribir_json(ruta_registro(ruta), registro, LIMITE_REGISTRO)
    for entrada in eventos:
        print(f"{entrada['estado']} | {entrada['id']} | {entrada['detalle']}")
    etiqueta = "Se incorporarían" if simular else "Incorporados"
    print(f"{etiqueta} {total} fragmentos, {composiciones} composiciones nuevas. Archivos evaluados: {intentados}.")
    return int(hubo_fallo)


def mostrar_estado(ruta, config):
    cfg = configuracion(config)
    datos = cargar_o_crear(ruta)
    estado = cargar_registro(ruta)
    print(f"Fuentes habilitadas: {', '.join(cfg['fuentes'])}. Intervalo: {cfg['intervalo_horas']} h.")
    print(f"Última sincronización: {estado['ultima'] or 'pendiente'}. Último éxito: {estado['ultimo_exito'] or 'pendiente'}.")
    print(f"Composiciones: {len({clave_cancion(p) for p in datos})}. Fragmentos: {len(datos)}.")
    licencias = sorted({p["licencia"] for p in datos if isinstance(p.get("licencia"), str)})
    print(f"Licencias registradas: {', '.join(licencias) or 'ninguna'}.")
    for entrada in estado["eventos"]:
        print(f"{entrada['fecha']} | {entrada['estado']} | {entrada['id']} | {entrada['detalle']}")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalogo", type=Path)
    parser.add_argument("--config", type=Path, default=CONFIGURACION)
    grupo = parser.add_mutually_exclusive_group(required=True)
    grupo.add_argument("--una-vez", action="store_true")
    grupo.add_argument("--si-pendiente", action="store_true")
    grupo.add_argument("--dry-run", action="store_true")
    grupo.add_argument("--estado", action="store_true")
    args = parser.parse_args(argv)
    try:
        ruta = resolver_catalogo(args.catalogo)
        if args.estado:
            mostrar_estado(ruta, args.config)
            return 0
        return sincronizar(ruta, configuracion(args.config), simular=args.dry_run, si_pendiente=args.si_pendiente)
    except (OSError, ValueError, RecursionError) as error:
        print(f"Error de sincronización: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
