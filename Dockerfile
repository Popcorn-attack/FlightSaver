FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY vendor ./vendor
COPY src ./src
RUN uv sync --frozen --no-dev --extra web --extra jev
ENV FLIGHTSAVER_HISTORY=/data/history.sqlite3 PORT=8000
VOLUME /data
EXPOSE 8000
# Set ANTHROPIC_API_KEY (required), FLIGHTSAVER_ACCESS_TOKEN (recommended when public),
# and optionally TYPESAFE_API_KEY for the Jev engine.
CMD ["sh", "-c", "uv run --no-sync uvicorn flightsaver.web.app:app --host 0.0.0.0 --port $PORT"]
