#!/usr/bin/env bash
set -euo pipefail
# Compatibility entry point; new architectures are selected through build.sh.
exec bash "$(dirname "$(realpath "$0")")/build.sh" arm64 "$@"
