# Dockerfile para deploy na Koyeb (ou qualquer plataforma que leia Dockerfile)
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Dependências de sistema para Pillow (imagens) e outros utilitários
RUN apt-get update && apt-get install -y --no-install-recommends \
    libjpeg62-turbo-dev zlib1g-dev && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

# Pastas que serão utilizadas para dados persistentes na Koyeb (via Volumes)
RUN mkdir -p /app/uploads /app/data

# Comando de inicialização (Koyeb fornece a porta em $PORT)
CMD gunicorn -w 2 -k gthread -b 0.0.0.0:${PORT:-8000} wsgi:app
