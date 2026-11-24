FROM ghcr.io/astral-sh/uv:python3.13-alpine

LABEL authors="sammy"

WORKDIR /src

RUN apk add --no-cache tzdata

COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-install-project --no-dev

COPY src ./src

CMD [".venv/bin/python", "src/main.py"]
