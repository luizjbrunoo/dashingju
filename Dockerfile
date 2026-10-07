FROM python:3.10.9-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

# opencv-python (não headless) e torch/docling precisam destas libs em runtime.
# gcc e libpq-dev não são instalados: psycopg[binary] já traz o driver.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libglib2.0-0 \
        libgl1 \
        libgomp1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

RUN useradd --create-home --uid 1000 --shell /usr/sbin/nologin appuser

COPY requirements.txt .
RUN pip install --upgrade pip \
    && pip install -r requirements.txt

COPY . .
RUN chmod +x deploy/web.sh deploy/worker.sh deploy/migrate.sh \
    && DJANGO_DEBUG=0 \
       DJANGO_SECRET_KEY=build-collectstatic-only \
       DJANGO_ALLOWED_HOSTS=localhost \
       python manage.py collectstatic --noinput \
    && mkdir -p /app/media /app/lancedb \
    && chown -R appuser:appuser /app

USER appuser

EXPOSE 8000

CMD ["./deploy/web.sh"]
