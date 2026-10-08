# syntax=docker/dockerfile:1.7

FROM ubuntu:24.04 AS tooling

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
        /opt/argon-filter-cache

# Export only source/toolchain pins, so Argon artifact metadata does not
# invalidate the expensive source layer. Identical output is copied by digest.
FROM tooling AS source-pins
COPY build-lock.json /tmp/build-lock.json
COPY scripts/image_source_inputs.py /tmp/image_source_inputs.py
RUN python3 /tmp/image_source_inputs.py --lock /tmp/build-lock.json > /tmp/source-lock.json

FROM tooling AS prepared
COPY --from=source-pins --chown=argon:argon /tmp/source-lock.json build-lock.json
COPY --chown=argon:argon .gclient common.sh ./
COPY --chown=argon:argon scripts/prepare_chromium.sh scripts/image_source_inputs.py \
    scripts/fetch_pinned_filter_lists.py scripts/fetch_pinned_extension.py ./scripts/
COPY --chown=argon:argon extensions/bundle.py extensions/BUILD.gn ./extensions/
# Seed pinned filter bytes even when no registry build cache exists.
COPY --chown=argon:argon docker/chromium/filter-cache/ /opt/argon-filter-cache/
USER argon

# Includes pinned Vanadium patches and dependency hooks in their original order.
# An Argon overlay change reuses this entire checkout, SDK and toolchain layer.
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt/lists,sharing=locked \
    ARGON_FETCH_VANADIUM=1 bash scripts/prepare_chromium.sh sources

COPY --chown=argon:argon patch.sh ./
COPY --chown=argon:argon scripts/apply_scoped_ca.py ./scripts/
COPY --chown=argon:argon chromium_overlay/ ./chromium_overlay/
COPY --chown=argon:argon certificates/ ./certificates/
COPY --chown=argon:argon extensions/ ./extensions/
COPY --chown=argon:argon res/ ./res/
RUN bash scripts/prepare_chromium.sh patch

# Runtime CI, signing and documentation changes do not reapply patches.
COPY --chown=argon:argon . .
RUN BUILD_MODE=prepare ARGON_PREPATCHED_SOURCES=1 BUILD_JOBS=2 bash build.sh arm64 \
    && rm -rf .build/policy-tests chromium/src/out

# Revision is metadata; it must not invalidate source preparation or tests.
ARG ARGON_REVISION=unknown
LABEL org.opencontainers.image.revision="${ARGON_REVISION}"
CMD ["/bin/bash"]
