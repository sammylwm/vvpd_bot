FROM ghcr.io/astral-sh/uv:python3.13-alpine

LABEL authors="sammy"

WORKDIR /src

ENV UV_NO_DEV=1

COPY pyproject.toml uv.lock ./

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-install-project --no-dev

COPY entrypoint.sh alembic.ini ./
RUN chmod +x entrypoint.sh

ENTRYPOINT ["sh", "./entrypoint.sh"]

COPY alembic ./alembic
COPY src ./src

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked
