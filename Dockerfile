# PocketSlice – OrcaSlicer CLI + FastAPI backend + PWA frontend in one image.
#
# Build args let you pin another OrcaSlicer release:
#   docker build --build-arg ORCA_VERSION=2.3.0 .
#   docker build --build-arg ORCA_APPIMAGE_URL=https://.../OrcaSlicer_Linux_....AppImage .
FROM ubuntu:24.04

ARG ORCA_VERSION=2.3.0
ARG ORCA_APPIMAGE_URL=""
ENV DEBIAN_FRONTEND=noninteractive

# Runtime libraries the OrcaSlicer AppImage links against (GTK/WebKit/GL) plus
# xvfb so the CLI has a display if it insists on one, plus Python for the app.
RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates curl xz-utils file \
      python3 python3-venv python3-pip \
      xvfb xauth \
      libgtk-3-0 libwebkit2gtk-4.1-0 libglu1-mesa libgl1 libegl1 libgles2 \
      libgstreamer1.0-0 libgstreamer-plugins-base1.0-0 \
      libdbus-1-3 libcurl4 libssl3 libxkbcommon0 libfontconfig1 libsecret-1-0 \
      libnotify4 libsoup-3.0-0 libglib2.0-0 libx11-6 libxrender1 libxi6 libxrandr2 \
      libxcursor1 libxinerama1 libxext6 libxfixes3 libxcomposite1 libxdamage1 \
      libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 libcups2 libdrm2 libgbm1 \
      libasound2t64 libpango-1.0-0 libcairo2 libgdk-pixbuf-2.0-0 \
      fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

# --- OrcaSlicer ---------------------------------------------------------------
WORKDIR /opt/orca
RUN set -eux; \
    if [ -n "$ORCA_APPIMAGE_URL" ]; then \
      candidates="$ORCA_APPIMAGE_URL"; \
    else \
      base="https://github.com/SoftFever/OrcaSlicer/releases/download/v${ORCA_VERSION}"; \
      candidates="$base/OrcaSlicer_Linux_AppImage_Ubuntu2404_V${ORCA_VERSION}.AppImage \
                  $base/OrcaSlicer_Linux_AppImage_Ubuntu2204_V${ORCA_VERSION}.AppImage \
                  $base/OrcaSlicer_Linux_AppImage_V${ORCA_VERSION}.AppImage \
                  $base/OrcaSlicer_Linux_V${ORCA_VERSION}.AppImage"; \
    fi; \
    ok=0; \
    for url in $candidates; do \
      echo "Trying $url"; \
      if curl -fSL --retry 3 -o orca.AppImage "$url"; then ok=1; break; fi; \
    done; \
    [ "$ok" = 1 ] || { echo "Could not download OrcaSlicer AppImage – set ORCA_APPIMAGE_URL"; exit 1; }; \
    chmod +x orca.AppImage; \
    ./orca.AppImage --appimage-extract >/dev/null; \
    rm orca.AppImage; \
    profiles="$(find squashfs-root -type d -path '*resources/profiles' | head -n1)"; \
    [ -n "$profiles" ] || { echo "resources/profiles not found in AppImage"; exit 1; }; \
    mkdir -p /opt/orca/resources; ln -s "/opt/orca/$profiles" /opt/orca/resources/profiles; \
    printf '%s\n' '#!/bin/sh' \
      '# Runs the extracted OrcaSlicer AppImage headless.' \
      'export APPDIR=/opt/orca/squashfs-root' \
      'export WEBKIT_DISABLE_COMPOSITING_MODE=1 LIBGL_ALWAYS_SOFTWARE=1' \
      'if [ -z "$DISPLAY" ]; then exec xvfb-run -a -s "-screen 0 1280x720x24" "$APPDIR/AppRun" "$@"; fi' \
      'exec "$APPDIR/AppRun" "$@"' > /opt/orca/orca-slicer; \
    chmod +x /opt/orca/orca-slicer; \
    /opt/orca/orca-slicer --help >/dev/null 2>&1 || echo "note: --help returned non-zero (often fine for the CLI)"

# --- App -----------------------------------------------------------------------
WORKDIR /app
COPY backend/requirements.txt /app/backend/requirements.txt
RUN python3 -m venv /venv && /venv/bin/pip install --no-cache-dir -r /app/backend/requirements.txt
COPY backend /app/backend
COPY frontend /app/frontend

ENV PATH="/venv/bin:$PATH" \
    DATA_DIR=/data \
    PROFILES_DIR=/profiles \
    ORCA_BIN=/opt/orca/orca-slicer \
    ORCA_SYSTEM_PROFILES=/opt/orca/resources/profiles \
    FRONTEND_DIR=/app/frontend \
    HOME=/data/home \
    PYTHONUNBUFFERED=1

VOLUME ["/data", "/profiles"]
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s CMD curl -fs http://127.0.0.1:8080/api/health || exit 1

WORKDIR /app/backend
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080", "--proxy-headers", "--forwarded-allow-ips=*"]
