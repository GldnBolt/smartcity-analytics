#!/usr/bin/env sh
# Recrea PostgreSQL y MongoDB de SmartCity desde cero (Linux / macOS / Git Bash).
#   sh cargar_todo.sh
# Requiere los datos crudos en data/raw/madrid (docker compose run --rm ingesta).
# Orden: PostgreSQL primero (fuente de verdad de los maestros), MongoDB después (los lee de PostgreSQL).
set -e
cd "$(dirname "$0")"
export MSYS_NO_PATHCONV=1  # evita que Git Bash reescriba /sql/... y /scripts/... como rutas de Windows

docker compose up -d --wait postgres mongo
# PostgreSQL (ELT): esquema (reinicialización destructiva) + extracción a staging + carga con DML
docker compose exec -T postgres psql -X -q -v ON_ERROR_STOP=1 -U smartcity -d smartcity -f /sql/01_esquema.sql
docker compose run --rm extractor_postgres
docker compose exec -T postgres psql -X -q -v ON_ERROR_STOP=1 -U smartcity -d smartcity -f /sql/02_carga.sql
# MongoDB: colecciones con validación + importación (lee maestros de PostgreSQL) + índices
docker compose exec -T mongo mongosh --quiet /scripts/00_esquema.js
docker compose run --rm importador
docker compose exec -T mongo mongosh --quiet /scripts/01_indices.js
