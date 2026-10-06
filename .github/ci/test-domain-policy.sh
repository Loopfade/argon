#!/usr/bin/env bash
# Compile the current checkout's validator tests with pinned Chromium sources.
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
src=${1:?Pass the prepared Chromium src directory}
depot=${2:?Pass the prepared depot_tools directory}
export PATH="$depot:$PATH"
export DEPOT_TOOLS_UPDATE=0

python3 "$ROOT/.github/ci/check-domain-test-inputs.py" \
  --lock "$ROOT/build-lock.json" --src "$src" --depot "$depot"

# Use this PR's header/tests even when the image predates the PR.
mkdir -p "$src/argon_tests" "$src/net/cert"
cp "$ROOT/tests/domain_policy/"* "$src/argon_tests/"
cp "$ROOT/chromium_overlay/net/cert/titanium_ru_domain_policy.h" \
   "$ROOT/chromium_overlay/net/cert/titanium_ru_domain_policy_unittest.cc" \
   "$src/net/cert/"

out="$src/out/Argon-domain-tests"
python3 "$ROOT/scripts/configure_build.py" --arch arm64 --output "$out/args.gn"
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
subprocess.run([str(binaries[0]), "--gtest_filter=TitaniumRuDomainPolicyTest.*"],
               check=True)
PY
