#!/usr/bin/env bash
set -euo pipefail
exec bash "$(dirname "$(realpath "$0")")/build-arm64.sh" "$@"
