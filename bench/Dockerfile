FROM python:3.12.11-slim-bookworm

COPY --from=ghcr.io/astral-sh/uv:0.13.4 /uv /uvx /usr/local/bin/
WORKDIR /workspace
COPY . .
RUN uv sync --project bench --locked --dev

ENTRYPOINT ["uv", "run", "--project", "bench", "--locked"]
