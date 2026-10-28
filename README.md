# SmartCity Analytics — Madrid (CE3101, II Semestre 2026)

Arquitectura de datos urbanos con **datos abiertos reales** del Ayuntamiento de Madrid y de la
EMT (2024–2025). Cada tecnología guarda el tipo de dato para el que es más adecuada.

| Motor | Qué guarda | Volumen |
|---|---|---:|
| **PostgreSQL** ([`postgres/`](postgres/README.md)) | Datos estructurados y relacionales: territorio (distritos, barrios), sensores, variables, umbrales, organismos y transporte público (rutas, paradas, recorridos, flota, demanda de viajeros). **Fuente de verdad de los maestros.** | ≈ 2,74 M filas |
| **MongoDB** ([`mongodb/`](mongodb/README.md)) | Datos semiestructurados de los sensores IoT: lecturas (cada estación mide variables distintas), alertas, eventos e incidentes con texto libre. | ≈ 2,19 M documentos |
| **Hadoop / Spark** | Histórico completo de tráfico (~320 M registros, ~19 GB) y procesamiento distribuido; Spark produce `mediciones_diarias` en PostgreSQL. | — |

## Flujo de datos

```
datos.madrid.es / datos.emtmadrid.es
        │  ingesta/descargar_madrid.py   (56 archivos, ~2,2 GB, con manifiesto de procedencia)
        ▼
  data/raw/madrid/
        │
        ├──► PostgreSQL   ELT: extractor → staging → 02_carga.sql (DML)   maestros + transporte EMT
        │         │
        │         ▼  (sensores, barrios, distritos y umbrales)
        └──► MongoDB      mongodb/importador  lecturas, alertas, eventos, incidentes
```

## Cómo recrear todo

Requisito único: Docker Desktop.

```powershell
docker compose run --rm ingesta     # 1. descarga los datos abiertos (una vez)
.\cargar_todo.ps1                   # 2. Windows: PostgreSQL y luego MongoDB (~3 min)
sh cargar_todo.sh                   #    Linux / macOS / Git Bash
```

`cargar_todo` es destructivo y repetible: reinicializa el esquema de PostgreSQL, extrae los
archivos crudos a staging, carga maestros y transporte con DML (`02_carga.sql`) y luego recrea
las colecciones de MongoDB, importa leyendo los maestros de PostgreSQL y crea los índices.

Conexiones locales:
- PostgreSQL: `postgresql://smartcity:smartcity@localhost:5432/smartcity`
- MongoDB: `mongodb://localhost:27017`, base `smartcity`

## Estructura

| Carpeta | Contenido |
|---|---|
| [`ingesta/`](ingesta/) | Descarga de datos abiertos, lectura común de archivos crudos y territorio (barrios, conversión UTM) |
| [`postgres/`](postgres/README.md) | Scripts SQL (esquema, carga, consultas, índices) y extractor de los archivos crudos a staging |
| [`mongodb/`](mongodb/README.md) | Scripts de mongosh (esquema, índices, CRUD, consultas, experimentos), importador y consultas para el dashboard |
| [`data/raw/`](data/raw/README.md) | Datos crudos (no versionados), fuentes y licencias |
| [`docker/python/`](docker/python/) | Imagen de Python que comparten la ingesta, el extractor y el importador |

## Fuentes y licencias

Ayuntamiento de Madrid (CC BY 4.0) y EMT de Madrid (condiciones de uso de la EMT y ODC-By). El
detalle de cada archivo está en [`data/raw/README.md`](data/raw/README.md). Lo único sintético es
el consumo eléctrico horario (no hay dato abierto) y el ciclo de atención de las alertas; ambos
están marcados como tales en los datos.
