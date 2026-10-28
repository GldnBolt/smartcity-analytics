# Datos crudos: Ayuntamiento de Madrid y EMT Madrid

Licencias (la de cada archivo queda registrada en `madrid/fuentes.json`):
- Datos del Ayuntamiento (aire, clima, tráfico, ruido, Calle 30, aparcamientos, límites
  administrativos): **CC BY 4.0**.
- Datos de la EMT (líneas, paradas, flota, GTFS): **Condiciones generales de uso de datos abiertos
  de la EMT de Madrid**.
- Demanda de viajeros de la EMT: **Open Data Commons Attribution License (ODC-By)**.

Todas exigen citar la fuente. Los archivos crudos no se versionan en git (ver `.gitignore`); este
README indica cómo obtenerlos.

## Descarga automática

```powershell
docker compose run --rm ingesta                       # todo
docker compose run --rm ingesta aire meteo            # solo algunos temas
```

El script [`ingesta/descargar_madrid.py`](../../ingesta/descargar_madrid.py) localiza cada recurso con el API
CKAN del portal, descarga, verifica el tamaño y la integridad de los ZIP, calcula el sha256 y
escribe `madrid/fuentes.json` con la procedencia de cada archivo. Si un archivo ya existe con el
tamaño correcto, lo omite.

> **Nota:** el portal puede responder `403 Forbidden` a descargas automatizadas. En ese caso, los
> archivos se descargan desde el navegador con los enlaces de abajo y se guardan con el nombre
> indicado.

## Archivos (2024–2025)

| Tema | Archivo destino (`data/raw/madrid/…`) | Recurso en el portal |
|---|---|---|
| Aire | `aire/estaciones_aire.csv` | [Estaciones de control](https://datos.madrid.es/dataset/212629-0-estaciones-control-aire) → CSV |
| Aire | `aire/aire_horario_2024.zip` | [Calidad del aire. Datos horarios](https://datos.madrid.es/dataset/201200-0-calidad-aire-horario) → "2024" ZIP |
| Aire | `aire/aire_horario_desde2025.csv` | mismo dataset → "Desde 2025" CSV |
| Clima | `meteo/estaciones_meteo.csv` | [Datos meteorológicos. Estaciones](https://datos.madrid.es/dataset/300360-0-meteorologicos-estaciones) → CSV |
| Clima | `meteo/meteo_horario_2024_MM.csv` (12 archivos, MM = 01…12) | [Datos meteorológicos horarios](https://datos.madrid.es/dataset/300352-0-meteorologicos-horarios) → "\<Mes\> 2024" CSV |
| Clima | `meteo/meteo_horario_desde2025.csv` | mismo dataset → "Desde 2025" CSV |
| Tráfico | `trafico/puntos_trafico_2024.csv`, `trafico/puntos_trafico_2025.csv` | [Ubicación de los puntos de medida](https://datos.madrid.es/dataset/202468-0-intensidad-trafico) → "Diciembre 2024" / "Diciembre 2025" CSV |
| Tráfico | `trafico/trafico_AAAA_MM.zip` (24 archivos, ~90 MB c/u) | [Histórico de datos del tráfico](https://datos.madrid.es/dataset/208627-0-transporte-ptomedida-historico) → "\<Mes\> 2024/2025" ZIP |
| Ruido | `ruido/estaciones_ruido.csv` | [Estaciones de medida acústicas](https://datos.madrid.es/dataset/211346-0-estaciones-acusticas) → CSV |
| Ruido | `ruido/ruido_diario_desde1998.csv` | [Contaminación acústica. Datos diarios](https://datos.madrid.es/dataset/215885-0-contaminacion-ruido) → CSV |
| Incidentes | `incidentes/calle30_2024.csv`, `incidentes/calle30_2025.csv` | [Calle 30. Histórico de incidencias y accidentes](https://datos.madrid.es/dataset/300201-0-calle30-accidentes-historico) → "2024" / "2025" CSV |
| Parking | `parking/ocupacion_parking_2024.csv`, `parking/ocupacion_parking_2025.csv` | [Aparcamientos. Histórico de ocupación](https://datos.madrid.es/dataset/300346-0-aparcamientos-ocupa-historico) → "2024" / "2025" CSV |
| Parking | `parking/aparcamientos_plazas.csv` | [Aparcamientos municipales. Número de plazas](https://datos.madrid.es/dataset/300542-0-aparcamientos-municipales) → CSV |
| Territorio | `territorio/barrios.topojson` | [Límites administrativos](https://datos.madrid.es/dataset/900012-0-limites-administrativos-mapas) → "Barrios municipales" JSON (TopoJSON, WGS84; 131 barrios, 21 distritos) |
| Transporte | `transporte/emt_lineas.csv` | [EMT. Líneas EMT](https://datos.madrid.es/dataset/900024-0-emt-lineas-autobus) → CSV |
| Transporte | `transporte/emt_paradas.csv` | [EMT. Paradas de EMT](https://datos.madrid.es/dataset/900023-0-emt-paradas-autobus) → CSV (paradas por línea, sentido y secuencia; coordenadas UTM) |
| Transporte | `transporte/emt_flota.csv` | [EMT. Histórico de flota de autobuses](https://datos.madrid.es/dataset/300241-0-emt-flota-autobuses) → CSV |
| Transporte | `transporte/emt_gtfs.zip` | [EMT. GTFS](https://datos.madrid.es/dataset/900030-0-emt-autobus-gtfs) → ZIP (routes, stops con lat/lon, trips, stop_times, shapes) |
| Transporte | `transporte/emt_demanda_linea_franja_2019_2025.zip` | [EMT. Demanda diaria de viajeros](https://datos.emtmadrid.es/dataset/demanda-diaria-viajeros-autobus) (catálogo de la EMT) → "2019_25_linea_franja.zip" |

## Volumen

56 archivos, ~2,2 GB comprimidos.

- Demanda de la EMT: ~2,7 M de filas (viajeros por línea, día y hora) en 2024–2025.
- Histórico de tráfico: ~13,5 M de mediciones por mes, ~320 M en 2024–2025 (~19 GB sin
  comprimir). Es la entrada principal de HDFS y Spark.
