FROM ghcr.io/astral-sh/uv:python3.12-alpine
LABEL maintainer="xdream oldlu <xdream@gmail.com>"

RUN apk add --no-cache curl
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

ADD . /app
WORKDIR /app
EXPOSE 8001
RUN uv sync --locked

# 
CMD ["uv", "run", "uvicorn", "main:app", "--proxy-headers", "--host", "0.0.0.0", "--port", "8001"]