"""
Descarga los datos abiertos reales del Ayuntamiento de Madrid usados por SmartCity.

Los archivos se buscan a través del API CKAN de datos.madrid.es y del catálogo propio de la EMT
(datos.emtmadrid.es), por dataset + descripción del recurso. Así el script no depende de URLs que
los portales cambian cada vez que publican un mes nuevo.

Destino:   data/raw/madrid/<tema>/...
Manifiesto: data/raw/madrid/fuentes.json  (dataset, título, licencia, URL, tamaño, sha256, fecha)

Uso:  python -m ingesta.descargar_madrid [tema ...]     # sin argumentos descarga todo
      temas: aire meteo trafico ruido incidentes parking territorio transporte
Es idempotente: si un archivo ya existe con el tamaño esperado, no se vuelve a descargar.
"""
import hashlib
import json
import re
import sys
import time
import urllib.parse
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

PORTALES = {"madrid": "https://datos.madrid.es", "emt": "https://datos.emtmadrid.es"}
DESTINO = Path("data/raw/madrid")
ANIOS = ("2024", "2025")
MESES = {"Enero": "01", "Febrero": "02", "Marzo": "03", "Abril": "04", "Mayo": "05", "Junio": "06",
         "Julio": "07", "Agosto": "08", "Septiembre": "09", "Octubre": "10", "Noviembre": "11",
         "Diciembre": "12"}
RE_MES_ANIO = r"(?P<mes>" + "|".join(MESES) + r") (?P<anio>" + "|".join(ANIOS) + r")$"
RE_ANIO = r"(?P<anio>" + "|".join(ANIOS) + r")$"

# (tema, dataset, regex sobre la descripción o el nombre del recurso, formato, plantilla del archivo)
# El dataset puede llevar prefijo de portal ("emt:..."); sin prefijo es datos.madrid.es.
FUENTES = [
    ("aire", "212629-0-estaciones-control-aire", r"Estaciones de control$", "CSV", "estaciones_aire.csv"),
    ("aire", "201200-0-calidad-aire-horario", r"desde 2001\. 2024$", "ZIP", "aire_horario_2024.zip"),
    ("aire", "201200-0-calidad-aire-horario", r"Desde 2025$", "CSV", "aire_horario_desde2025.csv"),

    ("meteo", "300360-0-meteorologicos-estaciones", r".", "CSV", "estaciones_meteo.csv"),
    ("meteo", "300352-0-meteorologicos-horarios", r"(?P<mes>" + "|".join(MESES) + r") (?P<anio>2024)$", "CSV",
     "meteo_horario_{anio}_{mm}.csv"),
    ("meteo", "300352-0-meteorologicos-horarios", r"Desde 2025$", "CSV", "meteo_horario_desde2025.csv"),

    ("trafico", "202468-0-intensidad-trafico", r"Diciembre " + RE_ANIO, "CSV", "puntos_trafico_{anio}.csv"),
    ("trafico", "208627-0-transporte-ptomedida-historico", RE_MES_ANIO, "ZIP", "trafico_{anio}_{mm}.zip"),

    ("ruido", "211346-0-estaciones-acusticas", r"Estaciones de medida$", "CSV", "estaciones_ruido.csv"),
    ("ruido", "215885-0-contaminacion-ruido", r"diarios", "CSV", "ruido_diario_desde1998.csv"),

    ("incidentes", "300201-0-calle30-accidentes-historico", r"accidentes\. " + RE_ANIO, "CSV", "calle30_{anio}.csv"),

    ("parking", "300346-0-aparcamientos-ocupa-historico", r"rotacionales " + RE_ANIO, "CSV", "ocupacion_parking_{anio}.csv"),
    ("parking", "300542-0-aparcamientos-municipales", r".", "CSV", "aparcamientos_plazas.csv"),

    # Límites oficiales de los 131 barrios (TopoJSON, WGS84): ubicación real de sensores y paradas
    ("territorio", "900012-0-limites-administrativos-mapas", r"^Barrios municipales$", "JSON", "barrios.topojson"),

    # Transporte público (EMT Madrid): rutas, paradas, flota y demanda real de viajeros
    ("transporte", "900024-0-emt-lineas-autobus", r"^Líneas EMT$", "CSV", "emt_lineas.csv"),
    ("transporte", "900023-0-emt-paradas-autobus", r"^Paradas de EMT$", "CSV", "emt_paradas.csv"),
    ("transporte", "300241-0-emt-flota-autobuses", r"flota", "CSV", "emt_flota.csv"),
    ("transporte", "900030-0-emt-autobus-gtfs", r"GTFS", "ZIP", "emt_gtfs.zip"),
    # El enlace del portal municipal está roto (404); el archivo vigente está en el catálogo de la EMT
    ("transporte", "emt:demanda-diaria-viajeros-autobus", r"linea_franja\.zip$", "ZIP",
     "emt_demanda_linea_franja_2019_2025.zip"),
]


# Si el portal responde 403, los archivos que falten se descargan a mano (ver data/raw/README.md).
CABECERAS = {"User-Agent": "SmartCity-TEC/1.0 (proyecto academico CE3101)"}


def paquete(portal, dataset_id, intentos=5):
    url = f"{PORTALES[portal]}/api/3/action/package_show?id={urllib.parse.quote(dataset_id)}"
    req = urllib.request.Request(url, headers=CABECERAS)
    for i in range(1, intentos + 1):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.load(r)["result"]
        except Exception as e:  # límite de frecuencia del portal: esperar y reintentar
            if i == intentos:
                raise
            print(f"  API ocupado ({e}), reintento {i} en {10 * i}s")
            time.sleep(10 * i)


def resolver(tema, dataset_id, patron, formato, plantilla):
    """Devuelve la lista de archivos a descargar para una entrada de FUENTES."""
    portal, _, dataset_id = dataset_id.rpartition(":")
    portal = portal or "madrid"
    p = paquete(portal, dataset_id)
    archivos, vistos = [], set()
    for r in p["resources"]:
        nombre_rec = (r.get("name") or "").strip()
        desc = (r.get("description") or nombre_rec).strip()
        if (r.get("format") or "").upper() != formato:
            continue
        m = re.search(patron, desc) or re.search(patron, nombre_rec)
        if not m:
            continue
        g = m.groupdict()
        nombre = plantilla.format(anio=g.get("anio", ""), mm=MESES.get(g.get("mes"), ""))
        if nombre in vistos:
            continue
        vistos.add(nombre)
        archivos.append({
            "tema": tema, "archivo": f"{tema}/{nombre}", "dataset": dataset_id, "titulo": p["title"],
            "recurso": desc, "licencia": p.get("license_title") or p.get("license_id"),
            "url": r["url"], "tamano_declarado": int(r["size"]) if r.get("size") else None,
            "portal": f"{PORTALES[portal]}/dataset/{dataset_id}",
        })
    if not archivos:
        print(f"  AVISO sin coincidencias: {dataset_id} / {patron} / {formato}")
    return archivos


def completo(ruta, es_zip, esperado=None):
    """Un corte de conexión puede dejar el archivo a medias sin lanzar error: se verifica el
    tamaño esperado y, para los ZIP, que su índice sea legible."""
    if not ruta.exists() or (esperado and ruta.stat().st_size != esperado):
        return False
    return not es_zip or zipfile.is_zipfile(ruta)


def bajar(url, tmp, es_zip, esperado=None, intentos=6):
    for i in range(1, intentos + 1):
        try:
            req = urllib.request.Request(url, headers=CABECERAS)
            with urllib.request.urlopen(req, timeout=300) as r, tmp.open("wb") as f:
                esperado = esperado or int(r.headers.get("Content-Length") or 0) or None
                while bloque := r.read(1 << 20):
                    f.write(bloque)
            if not completo(tmp, es_zip, esperado):
                raise IOError(f"descarga incompleta ({tmp.stat().st_size} de {esperado} bytes)")
            return
        except Exception as e:  # el portal a veces responde 403/5xx bajo carga: reintentar con espera
            if i == intentos:
                raise
            print(f"  reintento {i} ({e}) {url[-50:]}")
            time.sleep(15 * i)


def descargar(a):
    ruta = DESTINO / a["archivo"]
    ruta.parent.mkdir(parents=True, exist_ok=True)
    es_zip = ruta.suffix.lower() == ".zip"
    if completo(ruta, es_zip, a["tamano_declarado"]):
        estado = "ya existía"
    else:
        tmp = ruta.with_suffix(ruta.suffix + ".part")
        try:
            bajar(a["url"], tmp, es_zip, a["tamano_declarado"])
        except Exception as e:
            print(f"  FALLÓ       {a['archivo']}: {e}")
            return None
        tmp.replace(ruta)
        estado = "descargado"
        time.sleep(3)  # pausa de cortesía entre descargas
    h = hashlib.sha256()
    with ruta.open("rb") as f:
        while bloque := f.read(1 << 20):
            h.update(bloque)
    a.update(tamano=ruta.stat().st_size, sha256=h.hexdigest(),
             descargado=datetime.now(timezone.utc).isoformat(timespec="seconds"))
    print(f"  {estado:<11} {a['archivo']:<45} {a['tamano'] / 1048576:8.1f} MB")
    return a


def main():
    temas = set(sys.argv[1:]) or {f[0] for f in FUENTES}
    print("Resolviendo recursos en datos.madrid.es / datos.emtmadrid.es ...")
    archivos = [a for f in FUENTES if f[0] in temas for a in resolver(*f)]
    total = sum(a["tamano_declarado"] or 0 for a in archivos)
    print(f"{len(archivos)} archivos, ~{total / 1048576:,.0f} MB\n")

    with ThreadPoolExecutor(max_workers=1) as ex:  # una descarga a la vez: el portal limita la frecuencia
        resultados = [r for r in ex.map(descargar, archivos) if r]
    fallidos = len(archivos) - len(resultados)

    manifiesto = DESTINO / "fuentes.json"
    previos = json.loads(manifiesto.read_text(encoding="utf-8")) if manifiesto.exists() else []
    por_archivo = {a["archivo"]: a for a in previos}
    por_archivo.update({a["archivo"]: a for a in resultados})
    manifiesto.write_text(json.dumps(sorted(por_archivo.values(), key=lambda a: a["archivo"]),
                                     ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nManifiesto: {manifiesto}  ({len(por_archivo)} archivos)")
    if fallidos:
        sys.exit(f"{fallidos} archivo(s) fallaron; vuelve a ejecutar para reintentar solo esos.")


if __name__ == "__main__":
    main()
