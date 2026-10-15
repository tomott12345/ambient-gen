# syntax=docker/dockerfile:1.7
#
# ambient-gen: the web app (Go) + the generative engine (Python), built on
# the process-audio image, which brings ffmpeg, fonts, and the music
# pipeline used for masters, Shorts/Reels, visualizer videos and captions.
#
#   docker build -t ambient-gen .
#   docker run -d --name ambient-gen -p 127.0.0.1:8766:8766 -v ambient-gen-data:/data ambient-gen
#   open http://127.0.0.1:8766
#
# Publish the port on 127.0.0.1 only: the app has no login, and anyone who
# can reach the port can start jobs.
#
# Build args:
#   PROCESS_AUDIO_IMAGE   base image (default: the published one). To use a
#                         local checkout instead:
#                           docker build -t process-audio ../process-audio
#                           docker build --build-arg PROCESS_AUDIO_IMAGE=process-audio -t ambient-gen .

ARG PROCESS_AUDIO_IMAGE=ghcr.io/tomott12345/process-audio:latest
ARG GO_VERSION=1.24

# ---------------------------------------------------------------- Go build
FROM --platform=$BUILDPLATFORM golang:${GO_VERSION}-bookworm AS gobuild
ARG TARGETOS
ARG TARGETARCH
WORKDIR /src
COPY web/go.mod ./
COPY web/ ./
# static binary, cross-compiled for the target platform (no CGO, no QEMU)
RUN CGO_ENABLED=0 GOOS=$TARGETOS GOARCH=$TARGETARCH \
    go build -trimpath -ldflags="-s -w" -o /out/agweb ./cmd/agweb

# ---------------------------------------------------------------- runtime
FROM ${PROCESS_AUDIO_IMAGE} AS runtime

LABEL org.opencontainers.image.title="ambient-gen" \
      org.opencontainers.image.description="Generative ambient, lo-fi and techno songs, with masters, Shorts and visualizer videos" \
      org.opencontainers.image.source="https://github.com/tomott12345/ambient-gen"

USER root
# numpy/scipy/numba already come with the base image (librosa needs numba);
# listing them makes ambient-gen's requirements explicit. MIDI isn't used in
# a container, so mido/python-rtmidi are left out.
RUN pip install "numpy>=1.24" "scipy>=1.10" "numba>=0.58"

WORKDIR /ambient
COPY *.py ./
COPY presets/ ./presets/
COPY patches/ ./patches/
COPY web/release_bridge.py ./web/release_bridge.py
COPY --from=gobuild /out/agweb /usr/local/bin/agweb
RUN mkdir -p /data && chown -R app:app /data
USER app

VOLUME /data
EXPOSE 8766

HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
  CMD python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8766/api/health', timeout=4)" || exit 1

ENTRYPOINT ["agweb", "-ambient", "/ambient", "-process-audio", "/app", "-data", "/data", "-python", "python3", "-container"]
CMD ["-addr", "0.0.0.0:8766"]
