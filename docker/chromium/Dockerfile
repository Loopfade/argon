# syntax=docker/dockerfile:1.7

FROM ubuntu:24.04

ARG ARGON_REVISION=unknown

LABEL org.opencontainers.image.title="Argon prepared Chromium source"
LABEL org.opencontainers.image.description="Pinned Chromium checkout prepared for Argon Android builds"
LABEL org.opencontainers.image.source="https://github.com/Loopfade/argon"
LABEL org.opencontainers.image.revision="${ARGON_REVISION}"

ENV DEBIAN_FRONTEND=noninteractive \
    DEPOT_TOOLS_UPDATE=0 \
    ARGON_PREPARED_ROOT=/opt/argon

RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt/lists,sharing=locked \
    apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        git \
        python3 \
        sudo \
    && useradd --create-home --shell /bin/bash --uid 1001 argon \
    && printf 'argon ALL=(ALL) NOPASSWD:ALL\n' >/etc/sudoers.d/argon \
    && chmod 0440 /etc/sudoers.d/argon

WORKDIR /opt/argon
COPY --chown=argon:argon . .

USER argon

# BUILD_MODE=prepare performs the pinned Chromium/depot_tools checkout, gclient
# sync, build-dependency installation, hooks, Argon patches and policy tests,
# but deliberately does not compile an APK or populate ccache.
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt/lists,sharing=locked \
    BUILD_MODE=prepare BUILD_JOBS=2 bash build.sh arm64 \
    && rm -rf .build/policy-tests chromium/src/out

CMD ["/bin/bash"]
