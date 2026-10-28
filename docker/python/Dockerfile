# Imagen única para las herramientas Python del proyecto: descarga de datos (ingesta),
# carga de PostgreSQL y carga de MongoDB. El código se monta como volumen, no se copia.
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
