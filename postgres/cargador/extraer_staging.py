"""
Extracción (la E de ELT): copia a las tablas staging.* las filas leídas de los archivos crudos.

Python solo hace lo que SQL no puede: abrir ZIP, leer CSV irregulares, decodificar el TopoJSON de los
barrios, convertir coordenadas UTM y ubicar cada punto en su barrio. La transformación y la carga al
modelo están en postgres/sql/02_carga.sql, que se ejecuta después con psql.

Requiere el esquema creado (postgres/sql/01_esquema.sql). Vacía staging antes de copiar.

Uso:  python -m postgres.cargador.extraer_staging
"""
import os
import time

import psycopg

from . import maestros, transporte

PG_DSN = os.getenv("PG_DSN", "postgresql://smartcity:smartcity@localhost:5432/smartcity")

TABLAS = {
    "barrios": ("codigo", "nombre", "cod_distrito", "distrito", "latitud", "longitud"),
    "sensores": ("codigo", "nombre", "tipo_sensor", "red", "fecha_instalacion", "estado", "cod_barrio",
                 "latitud", "longitud", "fuente_datos"),
    "lineas": ("linea", "fecha_ini", "fecha_fin", "origen", "destino"),
    "gtfs_rutas": ("route_id", "nombre"),
    "recorridos": ("linea", "fecha_ini", "fecha_fin", "parada", "sentido", "secuencia"),
    "paradas": ("codigo", "nombre", "lat", "lon", "cod_barrio", "desde_utm"),
    "flota": ("vehiculo", "fecha_ini", "fecha_fin", "marca", "modelo", "combustible", "capacidad"),
    "demanda": ("fecha", "linea", "franja", "viajeros"),
}


def copiar(cur, tabla, filas):
    t0 = time.time()
    n = 0
    with cur.copy(f"COPY staging.{tabla} ({', '.join(TABLAS[tabla])}) FROM STDIN") as copy:
        for fila in filas:
            copy.write_row(fila)
            n += 1
    print(f"  staging.{tabla:<12} {n:>10,}   ({time.time() - t0:.0f}s)")


def main():
    t0 = time.time()
    sensores, omitidos = maestros.sensores()
    with psycopg.connect(PG_DSN) as con:
        with con.cursor() as cur:
            cur.execute("TRUNCATE " + ", ".join(f"staging.{t}" for t in TABLAS))
            print("Extracción a staging:")
            copiar(cur, "barrios", maestros.territorio())
            copiar(cur, "sensores", (s.fila() for s in sensores))
            if omitidos:
                print(f"    sensores omitidos (fuera del término municipal o sin coordenadas): {', '.join(omitidos)}")
            copiar(cur, "lineas", transporte.lineas())
            copiar(cur, "gtfs_rutas", transporte.gtfs_rutas())
            copiar(cur, "recorridos", transporte.recorridos())
            copiar(cur, "paradas", transporte.paradas())
            copiar(cur, "flota", transporte.flota())
            copiar(cur, "demanda", transporte.demanda())
    print(f"Staging listo en {time.time() - t0:.0f}s. Siguiente paso: postgres/sql/02_carga.sql")


if __name__ == "__main__":
    main()
