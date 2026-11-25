FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 UV_COMPILE_BYTECODE=1
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# libgomp1: LightGBM and XGBoost (the AI models) need the OpenMP runtime, which the slim image lacks
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 && rm -rf /var/lib/apt/lists/*

WORKDIR /srv
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

COPY . .
RUN useradd --create-home appuser && mkdir -p /srv/storage && chown -R appuser /srv
USER appuser

EXPOSE 8000
# --no-dev --frozen: do not try to install development packages or re-resolve dependencies when the container starts
CMD ["uv", "run", "--no-dev", "--frozen", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
