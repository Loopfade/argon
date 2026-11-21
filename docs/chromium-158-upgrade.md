# Chromium 158 transition — PR #93

This transition is isolated to PR #93. Do not merge the PR to test it and do
not change the `main` branch. The **Сборка Argon test из PR** workflow is
dispatched on `main` for the shared cache and signing context only; with
`pr_number=93` it pins and checks out the PR head SHA for image preparation,
ARM64/ARMv7 APK compilation and signature verification. Start a **new** run
after each code change: rerunning an old run retains its old SHA.

## Pinned source set

| Component | Pinned revision |
| --- | --- |
| Chromium | `158.0.8127.42`, `52bdd21723d5db0c9ab11716d38feac74828cf84` |
| Vanadium | `e017f70b2fbb4bfaef81a6b1c49ac51ff886aca2` |
| Titanium upstream reference | `9b3a181e48d2223c6482c855d1845ba51b5d0321` |
| BoringSSL (Chromium DEPS) | `ea9f3a34c3977144c45d272be81842fd891a0bdb` |

`build-lock.json` and the `vanadium` gitlink must agree with these pins.
`scripts/preflight.py --inputs-only` verifies that Vanadium's
`android_default_version_name` matches Chromium. The pinned Chromium tag and
DEPS must also agree with the lock, as checked by `tests/check_upstream_patches.py`.
The independently pinned depot_tools revision is unchanged.

## Integration adaptations

- `patch.sh` uses Titanium's M158 `VirtualDocumentPath` workaround with
  `ResolveResult/queryDocument`, rather than the old string-path API, and
  enables the upstream M158 local-network permission gate.
- Chromium 158 moved the top-toolbar capture constraints from the public
  `isDirty()` to the private `isDirty(boolean viewIsDirty, boolean allowInMotion)`.
  `scripts/apply_homepage.py` patches the new reviewed method for normal and
  in-motion captures. A pinned M158 fixture checks method integrity and
  idempotence. The old signature remains supported in its regression fixture.
- All Argon UA, RDS, background playback, explicit exit, extension,
  certificate, home page and Android JNI patches still require full
  post-Vanadium source checks before claiming M158 compatibility.
- The Chromium 158 / Vanadium source upgrade invalidates prepared image and
  compiler caches that depend on Chromium source inputs. Never relabel a
  Chromium 157 APK as a Chromium 158 test result.

- The M158 DEX probe accepts either verified record support or explicit older
  D8 record rejection while always validating the production exit DEX.
- The UA patch updates `WebContentsImpl` new-tab inheritance even when the
  desktop/mobile preset User-Agent string itself has not changed.

## Acceptance criteria

1. `validate.yml` is green on the exact current PR head; review source-patch
   checks, Android/Java source graphs, domain-policy tests and UI regressions.
2. Prepare a new Chromium 158 image and build **both** standalone test APKs
   from a new `Сборка Argon test из PR` run with `pr_number=93`. Verify the
   SHA, package identity, signer certificate, ARM64/ARMv7 outputs and `collect`.
3. On Android 13/14, reproduce 4PDA mobile → desktop → mobile and navigate
   inside the forum; inspect actual request UA, Client Hints and viewport.
4. Check the independent home → explicit Exit → relaunch flow after browsing or
   searching, both with and without close-tabs-on-exit; capture logcat on failure.
5. Check custom UA, extension/WebUI NTP, incognito, downloads, bookmarks, site
   exceptions, scoped CA, background playback and external navigation.

A pinned source transition is **not** proof of a functioning APK: keep the
PR open until CI, both APK builds and the Android acceptance scenarios pass.
