# Recrea PostgreSQL y MongoDB de SmartCity desde cero (Windows / PowerShell).
#   .\cargar_todo.ps1
# Requiere los datos crudos en data/raw/madrid (docker compose run --rm ingesta).
# Orden: PostgreSQL primero (fuente de verdad de los maestros), MongoDB después (los lee de PostgreSQL).
Set-Location $PSScriptRoot

function Paso([string]$cmd) {
    Write-Host "`n> $cmd" -ForegroundColor Cyan
    cmd /c "$cmd 2>&1"   # docker escribe progreso en stderr; se une a stdout
    if ($LASTEXITCODE -ne 0) { throw "Falló: $cmd" }
}

Paso "docker compose up -d --wait postgres mongo"
# PostgreSQL (ELT): esquema (reinicialización destructiva) + extracción a staging + carga con DML
Paso "docker compose exec -T postgres psql -X -q -v ON_ERROR_STOP=1 -U smartcity -d smartcity -f /sql/01_esquema.sql"
Paso "docker compose run --rm extractor_postgres"
Paso "docker compose exec -T postgres psql -X -q -v ON_ERROR_STOP=1 -U smartcity -d smartcity -f /sql/02_carga.sql"
# MongoDB: colecciones con validación + importación (lee maestros de PostgreSQL) + índices
Paso "docker compose exec -T mongo mongosh --quiet /scripts/00_esquema.js"
Paso "docker compose run --rm importador"
Paso "docker compose exec -T mongo mongosh --quiet /scripts/01_indices.js"
