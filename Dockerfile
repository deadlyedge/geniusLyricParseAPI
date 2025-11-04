FROM ghcr.io/astral-sh/uv:python3.12-alpine
LABEL maintainer="xdream oldlu <xdream@gmail.com>"

RUN apk add --no-cache curl
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

ADD . /app
WORKDIR /app
RUN uv sync --locked

# 
CMD ["uv", "run", "app.main:app", "--proxy-headers", "--host", "0.0.0.0", "--port", "8001"]