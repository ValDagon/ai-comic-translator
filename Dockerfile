# CPU image for cheap VPS: app API/worker + manga-image-translator (extract).
# Models download on first extract into the mit-models volume.

FROM python:3.11-slim-bookworm

RUN apt-get update && apt-get install -y --no-install-recommends \
        git \
        g++ \
        ffmpeg \
        libsm6 \
        libxext6 \
        libgl1 \
        libglib2.0-0 \
        fonts-dejavu-core \
        ca-certificates \
        gosu \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

ARG MIT_REPO_URL=https://github.com/zyddnys/manga-image-translator.git
# Pin: update after checking *_translations.txt still parses (see README).
ARG MIT_COMMIT=95227a2bb0fd306cd4f0c104d57284026f991b3a

RUN git clone "${MIT_REPO_URL}" /app/manga-image-translator \
    && cd /app/manga-image-translator \
    && git checkout "${MIT_COMMIT}" \
    && rm -rf .git

# Extract needs dumps + inpaint; upstream --save-text aborts with exit(-1).
COPY scripts/patch_mit_for_extract.py /tmp/patch_mit_for_extract.py
RUN python /tmp/patch_mit_for_extract.py /app/manga-image-translator \
    && rm /tmp/patch_mit_for_extract.py

RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir \
        torch torchvision \
        --index-url https://download.pytorch.org/whl/cpu

# Keep CPU torch; skip MIT's default (often CUDA) torch/torchvision lines.
RUN grep -viE '^(torch|torchvision)([=<[:space:]]|$)' \
        /app/manga-image-translator/requirements.txt \
        > /tmp/mit-requirements.txt \
    && pip install --no-cache-dir -r /tmp/mit-requirements.txt

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY . /app

# Runtime defaults without secrets (OPENROUTER_API_KEY comes from env).
COPY config.example.toml /app/config.toml
RUN sed -i 's/^api_key = .*/api_key = ""/' /app/config.toml \
    && sed -i 's|^# mit_repo = .*|mit_repo = "/app/manga-image-translator"|' /app/config.toml \
    && sed -i 's|^font_path = .*|font_path = "fonts/IrinaCTT.ttf"|' /app/config.toml \
    && mkdir -p /app/data /app/manga-image-translator/models

ENV MIT_REPO=/app/manga-image-translator \
    COMIC_DATA_DIR=/app/data \
    COMIC_DATABASE=/app/data/app.db \
    PYTHONUNBUFFERED=1

# Least privilege: don't run uvicorn/worker as root. The entrypoint (still
# run as root at container start) self-heals ownership of the mounted
# volumes — including ones written by an older root-user image — before
# dropping to `appuser` via gosu. See scripts/docker-entrypoint.sh.
RUN useradd --uid 1000 --create-home --shell /usr/sbin/nologin appuser \
    && chown -R appuser:appuser /app
COPY scripts/docker-entrypoint.sh /docker-entrypoint.sh
RUN chmod +x /docker-entrypoint.sh

EXPOSE 8000
ENTRYPOINT ["/docker-entrypoint.sh"]
CMD ["uvicorn", "api:app", "--host", "0.0.0.0", "--port", "8000"]
