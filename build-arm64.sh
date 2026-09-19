#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$(realpath "$0")")"
source ./common.sh

SIGNING_MODE=${SIGNING_MODE:-test}
case "$SIGNING_MODE" in test|release) ;; *) echo 'SIGNING_MODE must be test or release' >&2; exit 1;; esac
python3 scripts/preflight.py
python3 -m unittest discover -s tests -v
if [[ "$SIGNING_MODE" == release ]]; then
  : "${TITANIUM_RU_KEYSTORE_BASE64:?Missing release keystore}"
  : "${TITANIUM_RU_STORE_PASSWORD:?Missing release store password}"
  : "${TITANIUM_RU_KEY_PASSWORD:?Missing release key password}"
  : "${TITANIUM_RU_KEY_ALIAS:?Missing release alias}"
fi
if [[ -e chromium/src || -e depot_tools ]]; then
  echo 'Use a fresh dedicated checkout: chromium/src or depot_tools already exists.' >&2
  exit 1
fi

export VERSION
VERSION=$(python3 -c 'import json; print(json.load(open("build-lock.json"))["chromium_version"])')
CHROMIUM_REVISION=$(python3 -c 'import json; print(json.load(open("build-lock.json"))["chromium_commit"])')
DEPOT_REVISION=$(python3 -c 'import json; print(json.load(open("build-lock.json"))["depot_tools_commit"])')
export DEBIAN_FRONTEND=noninteractive
sudo dpkg --add-architecture i386
sudo apt-get update
sudo apt-get install -y git curl python3 python3-pil imagemagick librsvg2-bin cmake ninja-build openssl libgcc-s1:i386

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
export GIT_COMMITTER_NAME='Titanium RU Build'
export GIT_COMMITTER_EMAIL='titanium-ru-build@users.noreply.github.com'
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
git config user.name 'Titanium RU Build'
git config user.email 'titanium-ru-build@users.noreply.github.com'
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

mkdir -p out/TitaniumRuArm64
cp "$SCRIPT_DIR/args.gn" out/TitaniumRuArm64/args.gn
gn gen out/TitaniumRuArm64
autoninja -C out/TitaniumRuArm64 -j "${BUILD_JOBS:-4}" chrome_public_apk
mapfile -t apks < <(find out/TitaniumRuArm64/apks -maxdepth 1 -name 'Chrome*.apk' -type f)
[[ ${#apks[@]} == 1 ]] || { echo 'Expected one arm64 APK' >&2; exit 1; }
python3 "$SCRIPT_DIR/scripts/sign_and_verify.py" --apk "${apks[0]}" \
  --sdk "$PWD/third_party/android_sdk/public" --jdk "$PWD/third_party/jdk/current" \
  --mode "$SIGNING_MODE"
