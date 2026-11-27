#!/usr/bin/env bash
# Compile the current checkout's validator tests with pinned Chromium sources.
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
src=${1:?Pass the prepared Chromium src directory}
depot=${2:?Pass the prepared depot_tools directory}
export PATH="$depot:$PATH"
export DEPOT_TOOLS_UPDATE=0

configure_cache=()
if [[ -n ${CCACHE_DIR:-} ]]; then
  export CCACHE_BASEDIR="$src"
  toolchain=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["chromium_commit"])' "$ROOT/build-lock.json")
  export CCACHE_COMPILERCHECK="string:chromium-$toolchain"
  export CCACHE_DEPEND=true
  export CCACHE_DIRECT=true
  export CCACHE_NOHASHDIR=true
  export CCACHE_SLOPPINESS=modules,include_file_mtime,include_file_ctime
  ccache --set-config compression=true
  ccache --set-config compression_level=3
  ccache --max-size "${CCACHE_MAXSIZE:-1G}"
  ccache --zero-stats
  configure_cache+=(--ccache)
  trap 'ccache --show-stats' EXIT
fi

python3 "$ROOT/.github/ci/check-domain-test-inputs.py" \
  --lock "$ROOT/build-lock.json" --src "$src" --depot "$depot"

# Exercise actual D8 packaging without starting a cold Android target build.
python3 "$ROOT/.github/ci/check-exit-dex.py" --src "$src"

# This modified native observer belongs to //chrome/browser/content_settings:impl.
# Query its generated object on both ABIs; compile it on ARMv7 below.
rds_observer_object=obj/chrome/browser/content_settings/impl/request_desktop_site_web_contents_observer_android.o
# Compile the translation units for NTP state decoding and initial navigation.
session_restore_object=obj/components/sessions/sessions/content_serialized_navigation_builder.o
navigation_controller_object=obj/content/browser/browser/navigation_controller_impl.o
# Exercise the production Mojom/JNI bridge and extension recovery loaders.
android_regression_objects=(
  obj/third_party/blink/renderer/core/core/web_frame_widget_impl.o
  obj/content/browser/browser/render_frame_host_impl.o
  obj/content/browser/browser/page_impl.o
  obj/content/browser/browser/web_contents_android.o
  obj/content/browser/browser/web_contents_observer_proxy.o
  obj/chrome/browser/extensions/extensions/extension_service.o
  obj/chrome/browser/extensions/extensions/chrome_extension_registrar_delegate.o
  obj/extensions/browser/browser_sources/extension_registrar.o
  obj/extensions/browser/browser_sources/unpacked_installer.o
)

# Validate the full Android graphs for both APK architectures before narrowing
# the host-test graph. Query the same explicit JNI, Java and APK targets used by
# build.sh; missing targets must fail validation before the manual build starts.
# Compile the native WebUI objects below; APK verification belongs to the full build.
for arch in arm64 arm; do
  graph_out="$src/out/Argon-graph-$arch"
  python3 "$ROOT/scripts/configure_build.py" --arch "$arch" --output "$graph_out/args.gn" "${configure_cache[@]}"
  (
    cd "$src"
    gn gen "$graph_out"
    ninja -C "$graph_out" -t query \
      obj/chrome/browser/android/android/argon_certificate_domains_settings.o \
      obj/chrome/browser/android/android/argon_exit.o \
      obj/chrome/browser/ui/webui/argon_home/argon_home/argon_home_ui.o \
      obj/chrome/browser/ui/webui/argon_home/argon_home/argon_bookmarks_handler.o \
      obj/chrome/browser/ui/webui/argon_home/argon_home/argon_icon_handler.o \
      obj/chrome/browser/ui/webui/argon_home/argon_home/argon_theme_handler.o \
      obj/chrome/browser/ui/webui/argon_home/argon_home/argon_search_handler.o \
      "$rds_observer_object" \
      "$session_restore_object" \
      "$navigation_controller_object" \
      "${android_regression_objects[@]}" \
      obj/content/browser/browser/navigation_request.o \
      chrome_java chrome_public_apk > "$graph_out/argon-targets.txt"
  )
  echo "PASS: $arch Android graph contains the production JNI, Java and APK targets"
  python3 "$ROOT/.github/ci/check-exit-integration.py" --out "$graph_out" --java
done

# Compile the WebUI, theme and UA targets with the production toolchain: ARMv7 layout,
# navigation/subresource UA-CH, viewport scale restoration, RDS observer,
# ContentUtils JNI and complete browser Java sources.
(
  cd "$src"
  autoninja -C out/Argon-graph-arm -j 4 \
    obj/chrome/browser/ui/webui/argon_home/argon_home/argon_home_ui.o \
    obj/chrome/browser/ui/webui/argon_home/argon_home/argon_bookmarks_handler.o \
    obj/chrome/browser/ui/webui/argon_home/argon_home/argon_icon_handler.o \
    obj/chrome/browser/ui/webui/argon_home/argon_home/argon_theme_handler.o \
    obj/chrome/browser/ui/webui/argon_home/argon_home/argon_search_handler.o \
    obj/third_party/blink/renderer/core/core/computed_style.o \
    obj/chrome/browser/ui/android/night_mode/java.javac.jar \
    "$rds_observer_object" \
    "$session_restore_object" \
    "$navigation_controller_object" \
    "${android_regression_objects[@]}" \
    obj/chrome/browser/android/android/content_utils.o \
    obj/chrome/browser/android/android/argon_exit.o \
    obj/components/embedder_support/user_agent/user_agent_utils.o \
    obj/content/browser/browser/client_hints.o \
    obj/content/browser/browser/render_process_host_impl.o \
    obj/content/browser/browser/navigation_controller_android.o \
    obj/content/browser/browser/navigation_request.o \
    obj/content/browser/browser/web_contents_impl.o \
    obj/content/renderer/renderer/render_frame_impl.o \
    obj/content/renderer/renderer/document_state.o \
    obj/third_party/blink/renderer/core/core/frame_fetch_context.o \
    chrome_java
)
echo "PASS: homepage WebUI controller, icon, search and theme handlers compiled"
echo "PASS: restored NTP builder and navigation controller compiled"
echo "PASS: ARMv7 ComputedStyle size assertion"
echo "PASS: night mode Java module compiled"
echo "PASS: RDS observer compiled against pinned Chromium native headers"
echo "PASS: native Argon exit compiled against Chromium 158 per-profile archive API"
echo "PASS: User-Agent navigation, scale restoration, subresources, renderer flags, JNI and browser Java compiled"
echo "PASS: PWA color signals and extension recovery compiled"

# Use this PR's header/tests even when the image predates the PR.
mkdir -p "$src/argon_tests" "$src/net/cert" "$src/chrome/browser/net"
cp "$ROOT/tests/domain_policy/"* "$src/argon_tests/"
cp "$ROOT/chromium_overlay/net/cert/titanium_ru_domain_policy.h" \
   "$ROOT/chromium_overlay/net/cert/titanium_ru_domain_rules.h" \
   "$ROOT/chromium_overlay/net/cert/titanium_ru_domain_policy_unittest.cc" \
   "$src/net/cert/"
cp "$ROOT/chromium_overlay/chrome/browser/net/argon_ca_prefs.h" \
   "$ROOT/chromium_overlay/chrome/browser/net/argon_ca_policy.h" \
   "$src/chrome/browser/net/"

out="$src/out/Argon-domain-tests"
python3 "$ROOT/scripts/configure_build.py" --arch arm64 --output "$out/args.gn" "${configure_cache[@]}"
# Embed ICU tables so the host test uses actual IDN data without a runtime file.
sed -i -e 's/^use_siso = true$/use_siso = false/' \
       -e 's/^icu_use_data_file = true$/icu_use_data_file = false/' \
       -e 's/^generate_linker_map = true$/generate_linker_map = false/' "$out/args.gn"
cd "$src"
gn gen "$out" --root-target=//argon_tests:argon_domain_policy_tests \
  --root-pattern=//argon_tests:argon_domain_policy_tests
autoninja -C "$out" argon_domain_policy_tests -j 4

python3 - "$out" <<'PY'
import os
from pathlib import Path
import subprocess
import sys

binaries = [path for path in Path(sys.argv[1]).rglob("argon_domain_policy_tests")
            if path.is_file() and os.access(path, os.X_OK)]
if len(binaries) != 1:
    raise SystemExit(f"Expected one host test binary, found {len(binaries)}")
subprocess.run([str(binaries[0]),
                "--gtest_filter=TitaniumRuDomainPolicyTest.*:ArgonRussianCaPolicyTest.*"],
               check=True)
PY
