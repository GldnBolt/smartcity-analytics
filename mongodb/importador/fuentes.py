"""
Lectores de las fuentes reales de Madrid. Cada función produce documentos para `lecturas`
(sin `ubicacion`, que la agrega el cargador a partir del catálogo).

Formato "horario" de aire y clima del Ayuntamiento: una fila por estación × magnitud × día,
con 24 pares (Hnn = valor de la hora nn, Vnn = 'V' validado / 'N' no validado).
Hnn corresponde al periodo (nn-1):00–nn:00; `ts` guarda el inicio del periodo.
"""
import io
import math
import os
import random
import re
import zipfile
from collections import defaultdict
from datetime import timedelta
from multiprocessing import Pool

from ingesta.comun import RAW, en_rango, leer_csv, local_a_utc, numero, FECHA_INICIO, FECHA_FIN, ALMENDRA

AIRE_MAGNITUDES = {1: "so2", 6: "co", 7: "no", 8: "no2", 9: "pm25", 10: "pm10", 12: "nox", 14: "o3",
                   20: "tolueno", 30: "benceno", 35: "etilbenceno"}
METEO_MAGNITUDES = {81: "viento_ms", 82: "viento_dir", 83: "temperatura", 86: "humedad",
                    87: "presion_hpa", 88: "radiacion_wm2", 89: "precipitacion"}


# ---------------------------------------------------------------------------
# Aire y clima (formato horario H01..H24 / V01..V24)
# ---------------------------------------------------------------------------
def _agrupar_horario(filas, magnitudes, prefijo):
    """-> {(sensor_id, a, m, d, h): ({var: valor}, {var: valor_no_validado})}"""
    datos = defaultdict(lambda: ({}, {}))
    for f in filas:
        mag = int(f["MAGNITUD"])
        if mag not in magnitudes:
            continue
        a, m, d = int(f["ANO"]), int(f["MES"]), int(f["DIA"])
        if not en_rango(a, m, d):
            continue
        sensor, var = f"{prefijo}-{int(f['ESTACION']):03d}", magnitudes[mag]
        for h in range(1, 25):
            v = numero(f[f"H{h:02d}"])
            if v is None:
                continue
            validos, invalidos = datos[(sensor, a, m, d, h - 1)]
            (validos if f[f"V{h:02d}"] == "V" else invalidos)[var] = v
    return datos


def _docs_horarios(datos, tipo, extra_meta=None):
    for (sensor, a, m, d, h), (validos, invalidos) in sorted(datos.items()):
        if not validos:
            continue  # ninguna variable validada en esa hora: no hay lectura útil
        meta = {"fuente": "datos.madrid.es"}
        if extra_meta:
            meta.update(extra_meta.get(sensor, {}))
        if invalidos:
            meta["no_validados"] = invalidos  # dato crudo conservado; se excluye de los cálculos
        yield {"sensor_id": sensor, "tipo": tipo, "ts": local_a_utc(a, m, d, h),
               "mediciones": validos, "meta": meta}


def lecturas_aire(tipo_estacion):
    """tipo_estacion: {sensor_id: 'Urbana tráfico' | 'Urbana fondo' | 'Suburbana'}"""
    meta = {s: {"tipo_estacion": t} for s, t in tipo_estacion.items()}
    with zipfile.ZipFile(RAW / "aire/aire_horario_2024.zip") as z:
        for nombre in sorted(n for n in z.namelist() if n.endswith(".csv")):
            yield from _docs_horarios(_agrupar_horario(leer_csv(z.read(nombre)), AIRE_MAGNITUDES, "AIR"),
                                      "calidad_aire", meta)
    yield from _docs_horarios(_agrupar_horario(leer_csv(RAW / "aire/aire_horario_desde2025.csv"),
                                               AIRE_MAGNITUDES, "AIR"), "calidad_aire", meta)


def lecturas_clima():
    for mes in range(1, 13):
        ruta = RAW / f"meteo/meteo_horario_2024_{mes:02d}.csv"
        yield from _docs_horarios(_agrupar_horario(leer_csv(ruta), METEO_MAGNITUDES, "MET"), "clima")
    yield from _docs_horarios(_agrupar_horario(leer_csv(RAW / "meteo/meteo_horario_desde2025.csv"),
                                               METEO_MAGNITUDES, "MET"), "clima")


# ---------------------------------------------------------------------------
# Ruido (diario por periodo: D día, E tarde, N noche, T 24 h)
# ---------------------------------------------------------------------------
def lecturas_ruido():
    por_dia = defaultdict(dict)
    with (RAW / "ruido/ruido_diario_desde1998.csv").open(encoding="latin-1") as f:
        next(f)
        for linea in f:
            c = linea.rstrip("\r\n").split(";")
            if len(c) < 11 or not c[1].isdigit():
                continue
            a, m, d = int(c[1]), int(c[2]), int(c[3])
            if not en_rango(a, m, d):
                continue
            por_dia[(f"RUI-{int(c[0]):02d}", a, m, d)][c[4]] = [numero(x) for x in c[5:11]]
    nombres = {"D": "laeq_dia", "E": "laeq_tarde", "N": "laeq_noche", "T": "laeq_24h"}
    for (sensor, a, m, d), periodos in sorted(por_dia.items()):
        med = {nombres[p]: v[0] for p, v in periodos.items() if p in nombres and v[0] is not None}
        if "T" in periodos:  # percentiles del día completo (solo algunas estaciones los publican)
            for nombre, v in zip(("l1", "l10", "l50", "l90", "l99"), periodos["T"][1:]):
                if v is not None:
                    med[nombre] = v
        if med:
            yield {"sensor_id": sensor, "tipo": "ruido", "ts": local_a_utc(a, m, d),
                   "mediciones": med, "meta": {"fuente": "datos.madrid.es", "periodo": "diario"}}


# ---------------------------------------------------------------------------
# Estacionamientos (ocupación mensual por aparcamiento)
# ---------------------------------------------------------------------------
def lecturas_estacionamiento():
    for anio in ("2024", "2025"):
        for r in leer_csv(RAW / f"parking/ocupacion_parking_{anio}.csv"):
            if not (r["CÓDIDO PAR"] and r["AÑO"].isdigit() and r["MES"].isdigit()):
                continue  # filas de totales o vacías
            a, m = int(r["AÑO"]), int(r["MES"])
            med = {k: v for k, v in {
                "ocupacion_pct": numero(r["Ocupación (%)"]),
                "ocupacion_9_21_pct": numero(r["Ocupación 9h-21h (%)"]),
                "vehiculos_dia": numero(r["Vehículos día (media)"]),
                "plazas": numero(r["NUM PLAZAS"]),
            }.items() if v is not None}
            if med:
                yield {"sensor_id": f"PRK-{r['CÓDIDO PAR']}", "tipo": "estacionamiento",
                       "ts": local_a_utc(a, m, 1), "mediciones": med,
                       "meta": {"fuente": "datos.madrid.es", "periodo": "mensual",
                                "dias_calculo": int(numero(r["Días Cálculo"]) or 0)}}


# ---------------------------------------------------------------------------
# Tráfico (15 min -> agregado horario). Los ZIP mensuales se procesan en paralelo.
# ---------------------------------------------------------------------------
def _procesar_mes_trafico(args):
    ruta, ids = args
    acc = {}
    with zipfile.ZipFile(ruta) as z:
        miembro = next(n for n in z.namelist() if n.lower().endswith(".csv"))
        with z.open(miembro) as f:
            for linea in io.TextIOWrapper(f, encoding="latin-1"):
                i = linea.find(";")
                if linea[:i] not in ids:  # descarta rápido el ~99 % de los puntos no seleccionados
                    continue
                c = linea.rstrip("\r\n").split(";")
                clave = (linea[:i], c[1].strip('"')[:13])  # (id, 'AAAA-MM-DD HH')
                a = acc.get(clave)
                if a is None:
                    a = acc[clave] = [0, 0.0, 0, 0.0, 0, 0.0, 0, 0.0, 0, 0]
                a[0] += 1
                for j, col in ((1, 3), (3, 4), (5, 5), (7, 6)):  # intensidad, ocupacion, carga, vmed
                    v = numero(c[col].strip('"'))
                    if v is not None and v >= 0 and not (col == 6 and v == 0):
                        a[j] += v
                        a[j + 1] += 1
                if c[7].strip('"') != "N":
                    a[9] += 1
    return ruta.name, acc


def lecturas_trafico(tipo_elem, procesos=None):
    """tipo_elem: {id_punto: 'URB' | 'M30'} de los puntos seleccionados en el catálogo."""
    ids = set(tipo_elem)
    archivos = sorted((RAW / "trafico").glob("trafico_*.zip"))
    procesos = procesos or min(len(archivos), max(1, (os.cpu_count() or 2) - 1))
    with Pool(procesos) as pool:
        for nombre, acc in pool.imap_unordered(_procesar_mes_trafico, [(r, ids) for r in archivos]):
            n_docs = 0
            for (pid, hora), (n, s_int, n_int, s_ocu, n_ocu, s_car, n_car, s_vel, n_vel, n_err) in sorted(acc.items()):
                a, m, d, h = int(hora[:4]), int(hora[5:7]), int(hora[8:10]), int(hora[11:13])
                if not en_rango(a, m, d):
                    continue
                med = {}
                if n_int:
                    med["intensidad"] = round(s_int / n_int, 1)  # vehículos/hora
                if n_ocu:
                    med["ocupacion_pct"] = round(s_ocu / n_ocu, 1)
                if n_car:
                    med["carga"] = round(s_car / n_car, 1)
                    med["indice_congestion"] = round(s_car / n_car / 100, 3)
                if n_vel:  # solo los puntos de Calle 30 miden velocidad (vmed > 0)
                    med["velocidad_prom"] = round(s_vel / n_vel, 1)
                    med["tiempo_km_min"] = round(60 / (s_vel / n_vel), 2)
                if not med:
                    continue
                meta = {"fuente": "datos.madrid.es", "tipo_elem": tipo_elem[pid], "periodos": n}
                if n_err:
                    meta["periodos_con_error"] = n_err
                n_docs += 1
                yield {"sensor_id": f"TRF-{pid}", "tipo": "trafico", "ts": local_a_utc(a, m, d, h),
                       "mediciones": med, "meta": meta}
            print(f"    tráfico {nombre}: {n_docs:,} lecturas horarias")


# ---------------------------------------------------------------------------
# Energía: SINTÉTICA (no existe dato abierto horario) a partir de la temperatura REAL
# ---------------------------------------------------------------------------
def lecturas_energia(sensores_distrito, temperatura_por_hora, semilla=2026):
    """
    sensores_distrito: {sensor_id: cod_distrito}
    temperatura_por_hora: {ts_utc: temperatura media de las estaciones de Madrid}

    consumo = base_distrito · perfil_horario · factor_temperatura · factor_fin_de_semana · tendencia · ruido
      perfil_horario: valle nocturno, meseta al mediodía y pico a las 20-21 h
      factor_temperatura: calefacción bajo 16 °C y aire acondicionado sobre 24 °C
    """
    rng = random.Random(semilla)
    medidores = sorted(sensores_distrito.items())  # orden fijo: misma semilla -> mismos datos
    base = {s: (1200 if d in ALMENDRA else 1000) * rng.uniform(0.8, 1.2) for s, d in medidores}
    temp_previa = 15.0
    dia = FECHA_INICIO
    while dia <= FECHA_FIN:
        for h in range(24):
            ts = local_a_utc(dia.year, dia.month, dia.day, h)
            t = temperatura_por_hora.get(ts, temp_previa)
            temp_previa = t
            perfil = 0.55 + 0.25 * math.exp(-((h - 13) / 3) ** 2) + 0.45 * math.exp(-((h - 20.5) / 2) ** 2)
            f_temp = 1 + 0.035 * max(0.0, 16 - t) + 0.045 * max(0.0, t - 24)
            tendencia = 1 + 0.02 * (dia - FECHA_INICIO).days / 365
            for s, d in medidores:
                finde = (0.85 if d in ALMENDRA else 0.95) if dia.weekday() >= 5 else 1.0
                kwh = base[s] * perfil * f_temp * finde * tendencia * rng.uniform(0.95, 1.05)
                yield {"sensor_id": s, "tipo": "energia", "ts": ts,
                       "mediciones": {"consumo_kwh": round(kwh, 1), "demanda_kw": round(kwh * rng.uniform(1.05, 1.2), 1),
                                      "temperatura_ref": round(t, 1)},
                       "meta": {"fuente": "sintetico", "modelo": "f(temperatura real, hora, día)"}}
        dia += timedelta(days=1)


# ---------------------------------------------------------------------------
# Incidentes reales de Calle 30
# ---------------------------------------------------------------------------
def _slug(s):
    s = s.lower().replace("ó", "o").replace("í", "i").replace("á", "a").replace("é", "e").replace("ú", "u")
    return re.sub(r"[^a-z0-9]+", "_", s).strip("_") or "sin_clasificar"


def incidentes_calle30():
    for anio in ("2024", "2025"):
        for r in leer_csv(RAW / f"incidentes/calle30_{anio}.csv"):
            mf = re.match(r"(\d{1,2})/(\d{1,2})/(\d{4})", r["fecha"])
            mh = re.search(r"(\d{1,2}):(\d{2})(?::\d{2})?\s*$", r["hora"])
            if not mf:
                continue
            d, m, a = int(mf[1]), int(mf[2]), int(mf[3])
            if not en_rango(a, m, d):
                continue
            ts = local_a_utc(a, m, d, int(mh[1]) if mh else 0, int(mh[2]) if mh else 0)
            n = {k: int(numero(r[c]) or 0) for k, c in (
                ("vehiculos", "vehiculos implicados"), ("motocicletas", "motocicletas implicadas"),
                ("pesados", "pesados implicados"), ("heridos_leves", "heridos leves"),
                ("heridos_graves", "heridos graves"), ("victimas_mortales", "victimas mortales"))}
            detalle = {k: v for k, v in n.items() if v}  # solo lo que ocurrió: cada incidente es distinto
            for k, c in (("tipo_colision", "tipo colision"), ("estado_firme", "estado del firme"),
                         ("circulacion", "circulacion")):
                if r[c] and r[c] not in ("N/A", "0", "_", "(En blanco)", "Otro", "Otros"):
                    detalle[k] = r[c].capitalize()
            etiquetas = [e for e, cond in (
                ("tunel", r["ubicacion"].upper().startswith("T")),
                ("con_heridos", n["heridos_leves"] + n["heridos_graves"] > 0),
                ("victimas_mortales", n["victimas_mortales"] > 0),
                ("motocicleta", n["motocicletas"] > 0),
                ("vehiculo_pesado", n["pesados"] > 0),
                ("firme_mojado", r["estado del firme"] == "Mojado"),
                ("congestion", r["circulacion"].startswith("CONGESTIONADA")),
            ) if cond]
            prioridad = ("alta" if n["victimas_mortales"] or n["heridos_graves"]
                         else "media" if n["heridos_leves"] else "baja")
            doc = {
                "categoria": _slug(r["tipo accidente"]),
                "ts": ts,
                "numero": r["numero incidencia"],
                "descripcion": r["descripcion"] or r["tipo accidente"],
                "fuente": "Madrid Calle 30 (datos.madrid.es)",
                "prioridad": prioridad,
                "estado": "resuelto",
                "ubicacion": {"via": r["carretera"], "tramo": r["localizacion"], "eje": r["eje"],
                              "tipo_via": r["tipo via"].capitalize(), "entorno": r["ubicacion"].capitalize()},
                "detalle": detalle,
            }
            if etiquetas:
                doc["etiquetas"] = etiquetas
            yield doc
