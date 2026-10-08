#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$(realpath "$0")")"
source ./common.sh

if (( $# > 1 )); then
  echo 'Usage: bash build.sh [arm64|arm|x64|x86|arm64-v8a|armeabi-v7a|x86_64]' >&2
  exit 2
fi
if [[ ${1:-} == --help || ${1:-} == -h ]]; then
  echo 'Usage: bash build.sh [arm64|arm|x64|x86|arm64-v8a|armeabi-v7a|x86_64] (default: arm64)'
  exit 0
fi
TARGET_CPU=$(python3 scripts/configure_build.py --arch "${1:-arm64}" --print-cpu)
OUT_DIR="out/Argon-${TARGET_CPU}"

BUILD_MODE=${BUILD_MODE:-apk}
case "$BUILD_MODE" in
  apk|prepare|checkpoint|finish) ;;
  *) echo 'BUILD_MODE must be apk, prepare, checkpoint, or finish' >&2; exit 2;;
esac
BUILD_TIME_LIMIT_MINUTES=${BUILD_TIME_LIMIT_MINUTES:-240}
if [[ ! "$BUILD_TIME_LIMIT_MINUTES" =~ ^[1-9][0-9]*$ ]]; then
  echo 'BUILD_TIME_LIMIT_MINUTES must be a positive integer' >&2
  exit 2
fi
if [[ "$BUILD_MODE" == checkpoint && -z ${CCACHE_DIR:-} ]]; then
  echo 'CCACHE_DIR is required when BUILD_MODE=checkpoint' >&2
  exit 2
fi

SIGNING_MODE=${SIGNING_MODE:-test}
case "$SIGNING_MODE" in test|release) ;; *) echo 'SIGNING_MODE must be test or release' >&2; exit 1;; esac
preflight_args=(--arch "$TARGET_CPU")
if [[ "$BUILD_MODE" == finish || "$BUILD_MODE" == checkpoint || ${ARGON_PREPATCHED_SOURCES:-0} == 1 ]]; then
  preflight_args+=(--inputs-only)
fi
python3 scripts/preflight.py "${preflight_args[@]}"
# The image's source reuse flag must not reach fresh-checkout test fixtures.
env -u ARGON_PREPATCHED_SOURCES python3 -m unittest discover -s tests -v
if [[ ( "$BUILD_MODE" == apk || "$BUILD_MODE" == finish ) && "$SIGNING_MODE" == release ]]; then
  : "${TITANIUM_RU_KEYSTORE_BASE64:?Missing release keystore}"
  : "${TITANIUM_RU_STORE_PASSWORD:?Missing release store password}"
  : "${TITANIUM_RU_KEY_PASSWORD:?Missing release key password}"
  : "${TITANIUM_RU_KEY_ALIAS:?Missing release alias}"
fi
if [[ "$BUILD_MODE" != finish && "$BUILD_MODE" != checkpoint && ${ARGON_PREPATCHED_SOURCES:-0} != 1 && ( -e chromium/src || -e depot_tools ) ]]; then
  echo 'Use a fresh dedicated checkout: chromium/src or depot_tools already exists.' >&2
  exit 1
fi

if [[ "$BUILD_MODE" == finish || "$BUILD_MODE" == checkpoint ]]; then
  if [[ ! -d chromium/src || ! -d depot_tools ]]; then
    echo "BUILD_MODE=$BUILD_MODE requires a prepared Chromium checkout." >&2
    exit 1
  fi
  export PATH="$SCRIPT_DIR/depot_tools:$PATH"
  export DEPOT_TOOLS_UPDATE=0
  cd chromium/src
else
# BEGIN PREPARED SOURCES
  if [[ ${ARGON_PREPATCHED_SOURCES:-0} != 1 ]]; then
    bash scripts/prepare_chromium.sh sources
    bash scripts/prepare_chromium.sh patch
  fi
  bash scripts/prepare_chromium.sh validate
  export PATH="$SCRIPT_DIR/depot_tools:$PATH"
  export DEPOT_TOOLS_UPDATE=0
  cd chromium/src
# END PREPARED SOURCES
fi

configure_args=(--arch "$TARGET_CPU" --output "$OUT_DIR/args.gn")
if [[ -n ${CCACHE_DIR:-} ]]; then
  export CCACHE_DIR
  export CCACHE_BASEDIR="$PWD"
  CCACHE_TOOLCHAIN_ID=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["chromium_commit"])' "$SCRIPT_DIR/build-lock.json")
  export CCACHE_COMPILERCHECK="string:chromium-$CCACHE_TOOLCHAIN_ID"
  export CCACHE_DEPEND=true
  export CCACHE_DIRECT=true
  export CCACHE_NOHASHDIR=true
  export CCACHE_SLOPPINESS=modules,include_file_mtime,include_file_ctime
  mkdir -p "$CCACHE_DIR"
  ccache --set-config compression=true
  ccache --set-config compression_level=3
  ccache --max-size "${CCACHE_MAXSIZE:-7G}"
  configure_args+=(--ccache)
fi
python3 "$SCRIPT_DIR/scripts/configure_build.py" "${configure_args[@]}"
gn gen "$OUT_DIR"
if [[ "$BUILD_MODE" == prepare ]]; then
  # Publish the sources after patches, policy tests and GN generation. A
  # Docker RUN cannot checkpoint a partially compiled tree on a hosted-job
  # timeout; Android compilation belongs in the resumable APK workflow below.
  echo 'Chromium sources are prepared; Android compilation is checked by the APK build.'
  exit 0
fi
# Keep the injected JNI and Java targets explicit in every APK build mode.
# Their failures must prevent a successful APK build and signing, even if a
# future Chromium dependency change removes either from chrome_public_apk.
android_targets=(
  obj/chrome/browser/android/android/argon_certificate_domains_settings.o
  chrome_java
  chrome_public_apk
)
if [[ "$BUILD_MODE" == checkpoint ]]; then
  echo "Warming compiler cache for up to $BUILD_TIME_LIMIT_MINUTES minutes"
  build_started_at=$(date +%s)
  set +e
  timeout --signal=INT --kill-after=3m "${BUILD_TIME_LIMIT_MINUTES}m" \
    autoninja -C "$OUT_DIR" -j "${BUILD_JOBS:-4}" "${android_targets[@]}"
  build_status=$?
  set -e
  build_elapsed=$(( $(date +%s) - build_started_at ))
  if (( build_status == 137 && build_elapsed < BUILD_TIME_LIMIT_MINUTES * 60 )); then
    echo 'Build killed before its time limit (possible OOM); refusing automatic timeout recovery.' >&2
    exit "$build_status"
  fi
  ccache --show-stats
  case "$build_status" in
    0)
      mkdir -p "$SCRIPT_DIR/.build"
      touch "$SCRIPT_DIR/.build/cache-warm-complete-$TARGET_CPU"
      echo 'Cache warm-up reached the APK target.'
      ;;
    124|137)
      echo 'Cache warm-up time slice completed; the next slice will resume from the compiler cache.'
      ;;
    *) exit "$build_status" ;;
  esac
  exit 0
fi

autoninja -C "$OUT_DIR" -j "${BUILD_JOBS:-4}" "${android_targets[@]}"
mapfile -t apks < <(find "$OUT_DIR/apks" -maxdepth 1 -name 'Chrome*.apk' -type f)
[[ ${#apks[@]} == 1 ]] || { echo "Expected one $TARGET_CPU APK" >&2; exit 1; }
python3 "$SCRIPT_DIR/scripts/sign_and_verify.py" --apk "${apks[0]}" \
  --sdk "$PWD/third_party/android_sdk/public" --jdk "$PWD/third_party/jdk/current" \
  --mode "$SIGNING_MODE" --arch "$TARGET_CPU"
