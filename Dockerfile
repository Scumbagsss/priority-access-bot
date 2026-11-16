FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app
COPY requirements-postgres.txt .
RUN pip install --no-cache-dir -r requirements-postgres.txt \
    && groupadd --system appuser \
    && useradd --system --gid appuser --home /app appuser
COPY alembic.ini .
COPY postgres_app ./postgres_app
USER appuser
EXPOSE 8000
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn postgres_app.api:app_factory --factory --host 0.0.0.0 --port 8000 --no-proxy-headers"]
