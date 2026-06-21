FROM python:3.12-slim-bookworm

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg libopus0 \
    && rm -rf /var/lib/apt/lists/*

RUN useradd --create-home --shell /bin/bash melody

WORKDIR /app

COPY pyproject.toml README.md LICENSE ./
COPY src ./src

RUN pip install --no-cache-dir . \
    && python -c "from melody.mumble.pymumble_compat import install_ssl_wrap_socket_compat; install_ssl_wrap_socket_compat(); import ssl; assert hasattr(ssl, 'wrap_socket')" \
    && python -c "import melody; from pymumble_py3.errors import ConnectionRejectedError"

RUN mkdir -p /tmp/melody-buffer && chown melody:melody /tmp/melody-buffer

USER melody

VOLUME ["/tmp/melody-buffer"]

ENV PYTHONUNBUFFERED=1

CMD ["python", "-m", "melody"]
