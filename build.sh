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
  apk|warm|prepare|checkpoint|finish) ;;
  *) echo 'BUILD_MODE must be apk, warm, prepare, checkpoint, or finish' >&2; exit 2;;
esac
BUILD_TIME_LIMIT_MINUTES=${BUILD_TIME_LIMIT_MINUTES:-240}
if [[ ! "$BUILD_TIME_LIMIT_MINUTES" =~ ^[1-9][0-9]*$ ]]; then
  echo 'BUILD_TIME_LIMIT_MINUTES must be a positive integer' >&2
  exit 2
fi
if [[ "$BUILD_MODE" == warm && -z ${CCACHE_DIR:-} && -z ${SCCACHE_DIR:-} ]]; then
  echo 'CCACHE_DIR or SCCACHE_DIR is required when BUILD_MODE=warm' >&2
  exit 2
fi

SIGNING_MODE=${SIGNING_MODE:-test}
case "$SIGNING_MODE" in test|release) ;; *) echo 'SIGNING_MODE must be test or release' >&2; exit 1;; esac
preflight_args=(--arch "$TARGET_CPU")
if [[ "$BUILD_MODE" == finish || "$BUILD_MODE" == checkpoint ]]; then
  preflight_args+=(--inputs-only)
fi
python3 scripts/preflight.py "${preflight_args[@]}"
python3 -m unittest discover -s tests -v
if [[ ( "$BUILD_MODE" == apk || "$BUILD_MODE" == finish ) && "$SIGNING_MODE" == release ]]; then
  : "${TITANIUM_RU_KEYSTORE_BASE64:?Missing release keystore}"
  : "${TITANIUM_RU_STORE_PASSWORD:?Missing release store password}"
  : "${TITANIUM_RU_KEY_PASSWORD:?Missing release key password}"
  : "${TITANIUM_RU_KEY_ALIAS:?Missing release alias}"
fi
if [[ "$BUILD_MODE" != finish && "$BUILD_MODE" != checkpoint && ( -e chromium/src || -e depot_tools ) ]]; then
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
export VERSION
VERSION=$(python3 -c 'import json; print(json.load(open("build-lock.json"))["chromium_version"])')
CHROMIUM_REVISION=$(python3 -c 'import json; print(json.load(open("build-lock.json"))["chromium_commit"])')
DEPOT_REVISION=$(python3 -c 'import json; print(json.load(open("build-lock.json"))["depot_tools_commit"])')
export DEBIAN_FRONTEND=noninteractive
sudo dpkg --add-architecture i386
sudo apt-get update
sudo apt-get install -y git curl python3 python3-pil imagemagick librsvg2-bin cmake ninja-build openssl libgcc-s1:i386 ccache

git init depot_tools
git -C depot_tools remote add origin https://chromium.googlesource.com/chromium/tools/depot_tools.git
git -C depot_tools fetch --depth=1 origin "$DEPOT_REVISION"
git -C depot_tools checkout --detach "$DEPOT_REVISION"
export PATH="$SCRIPT_DIR/depot_tools:$PATH"
export DEPOT_TOOLS_UPDATE=0
# Disabling auto-updates also skips gclient's normal Python bootstrap. This
# initializes the pinned checkout without fetching a newer depot_tools commit.
bash "$SCRIPT_DIR/depot_tools/ensure_bootstrap"
"$SCRIPT_DIR/depot_tools/python-bin/python3" --version
[[ $(git -C "$SCRIPT_DIR/depot_tools" rev-parse HEAD) == "$DEPOT_REVISION" ]]
# gclient hooks run git am inside independent V8/search-engine repositories.
# Their commits do not inherit chromium/src/.git/config. Scope this identity
# to the build process and its children instead of changing global Git config.
export GIT_COMMITTER_NAME='Argon Build'
export GIT_COMMITTER_EMAIL='argon-build@users.noreply.github.com'
mkdir -p chromium/src .build/vanadium-patches
cp .gclient chromium/.gclient
cp vanadium/patches/*.patch .build/vanadium-patches/

# Preserve Titanium's patch selection without mutating the pinned submodule.
for pattern in '*trichrome-apk-build-targets.patch' '*trichrome-browser-apk-targets.patch' \
    '*detailed-language*.patch' '*supported-language*.patch' \
    '*javascript-optimizer-site-setting.patch' '*javascript-optimizer-settings-UI.patch' \
    '*component-updates.patch' '*pdf*.patch' '*PDF*.patch' '*for-content-public*.patch' \
    '*toolbar-button*.patch' '*configs-from-config-app*.patch' '*new-tab-card*.patch' \
    '*predictive-back*.patch'; do
  find .build/vanadium-patches -maxdepth 1 -type f -name "$pattern" -delete
done
replace "$SCRIPT_DIR/.build/vanadium-patches" VANADIUM TITANIUM
replace "$SCRIPT_DIR/.build/vanadium-patches" Vanadium Titanium
replace "$SCRIPT_DIR/.build/vanadium-patches" vanadium titanium

cd chromium/src
git init
git config user.name 'Argon Build'
git config user.email 'argon-build@users.noreply.github.com'
git remote add origin https://chromium.googlesource.com/chromium/src.git
git fetch --depth=1 origin "refs/tags/$VERSION"
[[ $(git rev-parse FETCH_HEAD) == "$CHROMIUM_REVISION" ]]
git checkout --detach "$CHROMIUM_REVISION"
git am --whitespace=nowarn --keep-non-patch "$SCRIPT_DIR"/.build/vanadium-patches/*.patch
gclient sync -D --no-history --nohooks
./build/install-build-deps.sh --no-prompt
gclient runhooks
source "$SCRIPT_DIR/patch.sh"
python3 "$SCRIPT_DIR/scripts/apply_scoped_ca.py" --chromium-src "$PWD"

BORINGSSL_REVISION=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["boringssl_commit"])' "$SCRIPT_DIR/build-lock.json")
[[ $(git -C third_party/boringssl/src rev-parse HEAD) == "$BORINGSSL_REVISION" ]]
cmake -S "$SCRIPT_DIR/tests" -B "$SCRIPT_DIR/.build/policy-tests" \
  -DBORINGSSL_SOURCE_DIR="$PWD/third_party/boringssl/src" -DCMAKE_BUILD_TYPE=Release
cmake --build "$SCRIPT_DIR/.build/policy-tests" --target scoped_ca_test -j "${BUILD_JOBS:-4}"
ctest --test-dir "$SCRIPT_DIR/.build/policy-tests" --output-on-failure
if [[ "$BUILD_MODE" == prepare ]]; then
  echo 'Chromium source tree is prepared; starting the compiler farm next.'
  exit 0
fi
fi

configure_args=(--arch "$TARGET_CPU" --output "$OUT_DIR/args.gn")
if [[ -n ${SCCACHE_DIR:-} ]]; then
  command -v sccache >/dev/null || {
    echo 'SCCACHE_DIR is set but sccache is not available' >&2
    exit 1
  }
  export SCCACHE_DIR
  mkdir -p "$SCCACHE_DIR"
  configure_args+=(--sccache)
elif [[ -n ${CCACHE_DIR:-} ]]; then
  export CCACHE_DIR
  export CCACHE_BASEDIR="$PWD"
  export CCACHE_COMPILERCHECK=content
  export CCACHE_NOHASHDIR=true
  export CCACHE_SLOPPINESS=include_file_mtime,include_file_ctime
  mkdir -p "$CCACHE_DIR"
  ccache --set-config compression=true
  ccache --set-config compression_level=6
  ccache --max-size "${CCACHE_MAXSIZE:-7G}"
  configure_args+=(--ccache)
fi
python3 "$SCRIPT_DIR/scripts/configure_build.py" "${configure_args[@]}"
gn gen "$OUT_DIR"
if [[ "$BUILD_MODE" == warm || "$BUILD_MODE" == checkpoint ]]; then
  echo "Warming compiler cache for up to $BUILD_TIME_LIMIT_MINUTES minutes"
  set +e
  timeout --signal=INT --kill-after=3m "${BUILD_TIME_LIMIT_MINUTES}m" \
    autoninja -C "$OUT_DIR" -j "${BUILD_JOBS:-4}" chrome_public_apk
  build_status=$?
  set -e
  if [[ -n ${SCCACHE_DIR:-} ]]; then
    sccache --show-stats || true
  else
    ccache --show-stats
  fi
  case "$build_status" in
    0)
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

autoninja -C "$OUT_DIR" -j "${BUILD_JOBS:-4}" chrome_public_apk
mapfile -t apks < <(find "$OUT_DIR/apks" -maxdepth 1 -name 'Chrome*.apk' -type f)
[[ ${#apks[@]} == 1 ]] || { echo "Expected one $TARGET_CPU APK" >&2; exit 1; }
python3 "$SCRIPT_DIR/scripts/sign_and_verify.py" --apk "${apks[0]}" \
  --sdk "$PWD/third_party/android_sdk/public" --jdk "$PWD/third_party/jdk/current" \
  --mode "$SIGNING_MODE" --arch "$TARGET_CPU"
