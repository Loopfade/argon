# Pinned filter snapshot

`47de51909886eaf08c3114b18496a98ac372b8cb2349dbba84b0ac02a7b168a6`
is the normalized `antiadblockfilters.txt` pinned by `build-lock.json`.
Its filename is its SHA-256; `fetch_pinned_filter_lists.py` checks the bytes
before use. The original list's copyright and license notices are retained.

The upstream URL changes in place. On 2026-09-24 this exact snapshot was recovered
from `ghcr.io/loopfade/argon-build@sha256:d8b7a4be25204388cbf3259635fed3f4d9eb94c240e09a37e3515b2ab3385cee`,
the image used for the verified release APK. Only its small filter-cache layer
was downloaded; both OCI digests and the filter's locked SHA-256 were checked.
Recovery run: https://github.com/Loopfade/argon/actions/runs/35977048663.

Commit the matching normalized snapshot here when changing this mutable input's
pin. Do not replace the expected digest with the latest network response just
to make a build pass. EasyList and EasyPrivacy already use immutable commit URLs.

For a local cold build, set
`ARGON_FILTER_LIST_CACHE_DIR="$PWD/docker/chromium/filter-cache"`
before running `build.sh`, so it can reuse this snapshot too.
