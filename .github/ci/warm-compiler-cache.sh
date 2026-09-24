#!/usr/bin/env bash
set -euo pipefail

elapsed=$(( $(date +%s) - BUILD_JOB_STARTED_AT ))
remaining=$(( BUILD_JOB_BUDGET_SECONDS - elapsed ))
# Reserve an uninterrupted final build for linking/Java tasks that ccache does
# not preserve, plus time to stop the container and upload the final snapshot.
required=$(( BUILD_CHECKPOINT_MINUTES * 60 + 180 + BUILD_FINISH_MINIMUM_SECONDS + BUILD_FINALIZE_RESERVE_SECONDS ))
if (( remaining < required )); then
  echo "::notice::Stopping cache slices with ${remaining}s left; reserving the final build window."
  mkdir -p .build
  touch ".build/cache-warm-slices-exhausted-${TARGET_ARCH}"
  echo 'complete=false' >> "$GITHUB_OUTPUT"
  exit 0
fi

env BUILD_JOBS="$(nproc)" bash .github/ci/run-in-build-container.sh
docker exec --env CCACHE_DIR=/ccache "$BUILD_CONTAINER_NAME" ccache --cleanup || true
if [[ -f ".build/cache-warm-complete-${TARGET_ARCH}" ]]; then
  echo 'complete=true' >> "$GITHUB_OUTPUT"
else
  echo 'complete=false' >> "$GITHUB_OUTPUT"
fi
