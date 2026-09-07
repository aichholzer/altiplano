# syntax=docker/dockerfile:1

# Altiplano's HTTP transport, on Alpine.
#
# Two entry points end up on the path. `altiplano-http` is the default command and
# serves the tools. `altiplano-clientkey` registers the clients allowed to call it,
# and it writes to the same directory the store lives in.
#
# Pin ALPINE_VERSION for a reproducible build:
#   docker build --build-arg ALPINE_VERSION=3.22 .

ARG ALPINE_VERSION=latest

# --- build the virtual environment -------------------------------------------
FROM alpine:${ALPINE_VERSION} AS build

# `uv` resolves and installs. It is in Alpine's community repository, which keeps
# this stage to one `apk` call and pulls no installer script over the network.
RUN apk add --no-cache python3 uv

WORKDIR /src

# The metadata first. A change to the source alone then reuses the dependency layer.
COPY pyproject.toml README.md ./
COPY src ./src

# The venv is the only thing the runtime stage takes. Nothing else in this stage
# survives, and `--no-cache` keeps the wheel cache out of the layer anyway.
RUN uv venv /opt/altiplano \
 && VIRTUAL_ENV=/opt/altiplano uv pip install --no-cache .

# --- runtime ------------------------------------------------------------------
FROM alpine:${ALPINE_VERSION}

# `addgroup` before `adduser -G`. BusyBox puts a system user in `nogroup` otherwise,
# and the ownership below would then be wrong in a way nothing complains about.
RUN apk add --no-cache python3 \
 && addgroup -S altiplano \
 && adduser -S -D -G altiplano -h /var/lib/altiplano altiplano \
 && chown altiplano:altiplano /var/lib/altiplano \
 && chmod 700 /var/lib/altiplano

COPY --from=build /opt/altiplano /opt/altiplano

# HOME has to resolve, and this is not optional. `config.py` computes
# `Path.home() / ".config" / "altiplano" / "env"` as the default argument to
# `os.environ.get`. That runs on import whether or not ALTIPLANO_CONFIG is set, and a
# uid with no passwd entry and no HOME fails at `import altiplano.config`.
#
# The bind address is every interface. A published port reaches nothing otherwise.
# Altiplano counts that as a reachable bind and requires one registered client before
# the first start. DEPLOYMENT.md has the order.
ENV HOME=/var/lib/altiplano \
    PATH=/opt/altiplano/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    ALTIPLANO_CONFIG=/var/lib/altiplano/env \
    ALTIPLANO_CLIENTS=/var/lib/altiplano/clients \
    ALTIPLANO_HTTP_HOST=0.0.0.0 \
    ALTIPLANO_HTTP_PORT=8000

# The store, its lock, and the temporary file each write goes through. Mount a volume
# here or every client token is lost with the container. No `VOLUME` declaration: it
# would hand every `docker run` an anonymous volume, and the compose file names one.
WORKDIR /var/lib/altiplano
USER altiplano

EXPOSE 8000

# Altiplano serves no unauthenticated endpoint. Every request without a registered
# token gets a 401, which leaves no 200 for a healthcheck to read. A TCP connect
# reports whether the listener is up.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python3 -c "import os,socket; socket.create_connection(('127.0.0.1', int(os.environ['ALTIPLANO_HTTP_PORT'])), 3).close()"

CMD ["altiplano-http"]
