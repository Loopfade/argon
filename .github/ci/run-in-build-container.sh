#!/usr/bin/env bash
set -euo pipefail

container=${BUILD_CONTAINER_NAME:-argon-build}
: "${TARGET_ARCH:?TARGET_ARCH is required}"

docker_args=(
  --env "BUILD_MODE=${BUILD_MODE:-apk}"
  --env "TARGET_ARCH=$TARGET_ARCH"
  --env "BUILD_JOBS=${BUILD_JOBS:-4}"
  --env "BUILD_TIME_LIMIT_MINUTES=${BUILD_TIME_LIMIT_MINUTES:-240}"
  --env "CCACHE_DIR=/ccache"
  --env "CCACHE_MAXSIZE=${CCACHE_MAXSIZE:-7G}"
)

for name in SIGNING_MODE TITANIUM_RU_KEYSTORE_BASE64 \
  TITANIUM_RU_STORE_PASSWORD TITANIUM_RU_KEY_PASSWORD \
  TITANIUM_RU_KEY_ALIAS; do
  if [[ -v $name ]]; then
    # Passing only the name makes docker copy the value without putting a
    # signing secret in this script's command-line construction.
    docker_args+=(--env "$name")
  fi
done

if [[ -n ${BUILD_EXEC_TIMEOUT_SECONDS:-} ]]; then
  docker_args+=(--env "BUILD_EXEC_TIMEOUT_SECONDS=$BUILD_EXEC_TIMEOUT_SECONDS")
fi

# The checkout is bind-mounted at /workspace; ensure build markers can be
# written back to the runner even when the prepared image removed .build.
mkdir -p "${GITHUB_WORKSPACE:-$PWD}/.build"

exec docker exec "${docker_args[@]}" "$container" bash -lc '
  set -euo pipefail
  cd /workspace
  if [[ -n ${BUILD_EXEC_TIMEOUT_SECONDS:-} ]]; then
    exec timeout --signal=INT --kill-after=3m \
      "${BUILD_EXEC_TIMEOUT_SECONDS}s" \
      env BUILD_JOBS="$BUILD_JOBS" bash build.sh "$TARGET_ARCH"
  fi
  exec env BUILD_JOBS="$BUILD_JOBS" bash build.sh "$TARGET_ARCH"
'
