FROM python:3.12-slim-bookworm

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg libopus0 \
    && rm -rf /var/lib/apt/lists/*

RUN useradd --create-home --shell /bin/bash melody

WORKDIR /app

COPY pyproject.toml README.md LICENSE ./
COPY src ./src

RUN pip install --no-cache-dir . \
    && python -c "import melody; from mumble.errors import ConnectionRejectedError"

USER melody

ENV PYTHONUNBUFFERED=1

CMD ["python", "-m", "melody"]
