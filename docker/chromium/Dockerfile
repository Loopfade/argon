# syntax=docker/dockerfile:1.7

FROM ubuntu:24.04

ARG ARGON_REVISION=unknown

LABEL org.opencontainers.image.title="Argon prepared Chromium source"
LABEL org.opencontainers.image.description="Pinned Chromium checkout prepared for Argon Android builds"
LABEL org.opencontainers.image.source="https://github.com/Loopfade/argon"

ENV DEBIAN_FRONTEND=noninteractive \
    DEPOT_TOOLS_UPDATE=0 \
    ARGON_PREPARED_ROOT=/opt/argon \
    ARGON_FILTER_LIST_CACHE_DIR=/opt/argon-filter-cache

# Bootstrap commands used by build.sh and Chromium's pinned
# install-build-deps.py before it can install the remaining packages.
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt/lists,sharing=locked \
    apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        file \
        git \
        lsb-release \
        python3 \
        sudo \
    && useradd --create-home --shell /bin/bash --uid 1001 argon \
    && printf 'argon ALL=(ALL) NOPASSWD:ALL\n' >/etc/sudoers.d/argon \
    && chmod 0440 /etc/sudoers.d/argon

WORKDIR /opt/argon
RUN chown argon:argon /opt/argon \
    && install -d -o argon -g argon \
        /opt/argon-cache/scripts /opt/argon-filter-cache

# This layer changes only when the downloader or pinned digests change. The
# normalized payloads are named by SHA-256 and verified again before every use.
COPY --chown=argon:argon build-lock.json /opt/argon-cache/build-lock.json
COPY --chown=argon:argon scripts/fetch_pinned_filter_lists.py \
    /opt/argon-cache/scripts/fetch_pinned_filter_lists.py

USER argon
RUN python3 /opt/argon-cache/scripts/fetch_pinned_filter_lists.py \
        --output /opt/argon-cache/verified-filter-lists.txt \
        --cache-dir "$ARGON_FILTER_LIST_CACHE_DIR" \
    && rm -rf /opt/argon-cache

LABEL org.opencontainers.image.revision="${ARGON_REVISION}"

COPY --chown=argon:argon . .

# BUILD_MODE=prepare performs the pinned Chromium/depot_tools checkout, gclient
# sync, build-dependency installation, hooks, Argon patches and policy tests,
# but deliberately does not compile an APK or populate ccache.
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt/lists,sharing=locked \
    BUILD_MODE=prepare BUILD_JOBS=2 bash build.sh arm64 \
    && rm -rf .build/policy-tests chromium/src/out

CMD ["/bin/bash"]
