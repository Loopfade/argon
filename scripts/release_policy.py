#!/usr/bin/env python3
"""Pure release-publishing policy checks used by GitHub Actions."""
import argparse
import hashlib
import json
from pathlib import Path
import sys


DOCUMENTATION_FILES = {
    "README.md",
    "README.upstream.md",
    "VALIDATION.md",
    "BUILDING.md",
    "index.html",
    "dashboard-data.json",
    ".nojekyll",
    ".github/package-metadata/argon-build-description.txt",
    ".github/workflows/update-dashboard.yml",
    ".github/ci/update-dashboard.py",
    ".github/workflows/deploy-dashboard.yml",
}
DOCUMENTATION_PREFIXES = ("docs/",)
GITHUB_COMPARE_FILE_LIMIT = 300
DEFAULT_RELEASE_ABI = "arm64-v8a"
ARMV7_RELEASE_ABI = "armeabi-v7a"
RELEASE_APK_GLOB = f"Argon-*-release-{DEFAULT_RELEASE_ABI}.apk"
RELEASE_LICENSES = (
    "Chromium-LICENSE.txt",
    "Ruthenium-LICENSE.txt",
    "Titanium-LICENSE.txt",
)
RELEASE_PAYLOAD_FILES = {
    ".gclient",
    "args.gn",
    "build-lock.json",
    "build.sh",
    "common.sh",
    "patch.sh",
    "vanadium",
    "docker/chromium/Dockerfile",
    "scripts/VerifyReleaseKey.java",
    "scripts/apply_scoped_ca.py",
    "scripts/configure_build.py",
    "scripts/fetch_pinned_extension.py",
    "scripts/fetch_pinned_filter_lists.py",
    "scripts/preflight.py",
    "scripts/prepare_chromium.sh",
    "scripts/image_source_inputs.py",
    "scripts/sign_and_verify.py",
}
RELEASE_PAYLOAD_PREFIXES = (
    "certificates/",
    "chromium_overlay/",
    "extensions/",
    "res/",
    "vanadium/",
)


def is_documentation_path(path: str) -> bool:
    return path in DOCUMENTATION_FILES or path.startswith(DOCUMENTATION_PREFIXES)


def _changed_paths(compare: dict) -> list[str]:
    """Include both sides of a rename and reject incomplete comparisons.

    GitHub's compare endpoint reports at most 300 files. Exactly hitting that
    limit is treated as unsafe because additional non-documentation files may
    have been omitted.
    """
    status = compare.get("status")
    if status == "identical":
        return []
    if status != "ahead":
        raise ValueError(f"<compare-status:{status}>")

    files = compare.get("files")
    if not isinstance(files, list):
        raise ValueError("<missing-files>")
    if len(files) >= GITHUB_COMPARE_FILE_LIMIT:
        raise ValueError("<compare-file-limit>")

    paths = []
    for item in files:
        if (not isinstance(item, dict)
                or not isinstance(item.get("filename"), str)
                or not item["filename"]):
            raise ValueError("<invalid-file-entry>")
        paths.append(item["filename"])
        if item.get("status") == "renamed" or "previous_filename" in item:
            previous = item.get("previous_filename")
            if not isinstance(previous, str) or not previous:
                raise ValueError("<invalid-rename-entry>")
            paths.append(previous)
    return list(dict.fromkeys(paths))


def unsafe_main_drift(compare: dict) -> list[str]:
    """Return changed paths that make an older build stale."""
    try:
        return [path for path in _changed_paths(compare)
                if not is_documentation_path(path)]
    except ValueError as error:
        return [str(error)]


def is_release_payload_path(path: str) -> bool:
    return path in RELEASE_PAYLOAD_FILES or path.startswith(RELEASE_PAYLOAD_PREFIXES)


def unsafe_release_payload_drift(compare: dict) -> list[str]:
    """Return changes that can alter an APK payload or its signing identity."""
    try:
        return [path for path in _changed_paths(compare)
                if is_release_payload_path(path)]
    except ValueError as error:
        return [str(error)]


def locate_release_artifact(
    root: Path, abi: str = DEFAULT_RELEASE_ABI
) -> dict[str, Path]:
    """Locate exactly one verified release payload for one Android ABI."""
    pattern = f"Argon-*-release-{abi}.apk"
    candidates = sorted(path for path in root.rglob(pattern) if path.is_file())
    if len(candidates) != 1:
        label = "arm64" if abi == DEFAULT_RELEASE_ABI else abi
        raise ValueError(
            f"expected exactly one release {label} APK, found {len(candidates)}"
        )

    apk = candidates[0]
    asset_dir = apk.parent
    selected = {
        "asset_dir": asset_dir,
        "apk": apk,
        "checksum": Path(f"{apk}.sha256"),
        "metadata": asset_dir / "build-info.json",
        "chromium_license": asset_dir / "Chromium-LICENSE.txt",
        "ruthenium_license": asset_dir / "Ruthenium-LICENSE.txt",
        "titanium_license": asset_dir / "Titanium-LICENSE.txt",
    }
    for name, path in selected.items():
        if name == "asset_dir":
            continue
        if not path.is_file():
            raise ValueError(f"missing required release artifact: {path}")
    return selected


def _file_digests(directory: Path) -> dict[str, str]:
    files = [path for path in directory.iterdir() if path.is_file()]
    return {
        path.name: "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
        for path in files
    }


def verify_existing_release(
    release: dict, target_sha: str, assets: Path, allow_missing_armv7: bool = False
) -> None:
    if release.get("draft"):
        raise ValueError("existing release is still a draft")
    if release.get("target_commitish") != target_sha:
        raise ValueError("existing release points at a different target commit")

    expected = _file_digests(assets)
    raw_assets = release.get("assets")
    if not isinstance(raw_assets, list):
        raise ValueError("existing release has no asset list")

    actual = {}
    for asset in raw_assets:
        if not isinstance(asset, dict):
            raise ValueError("invalid release asset metadata")
        name = asset.get("name")
        digest = asset.get("digest")
        if not isinstance(name, str) or not isinstance(digest, str) or name in actual:
            raise ValueError("invalid or duplicate release asset metadata")
        actual[name] = digest

    actual_names = set(actual)
    expected_names = set(expected)
    if actual_names - expected_names:
        raise ValueError("existing release asset names do not match the verified asset set")

    missing = expected_names - actual_names
    if missing:
        if not allow_missing_armv7:
            raise ValueError("existing release is missing verified assets")
        arm64_apks = [
            name for name in expected_names if name.endswith("-release-arm64-v8a.apk")
        ]
        if len(arm64_apks) != 1:
            raise ValueError("cannot derive missing armv7 release asset names")
        armv7_apk = arm64_apks[0].replace(
            "-release-arm64-v8a.apk", "-release-armeabi-v7a.apk"
        )
        if missing != {armv7_apk, f"{armv7_apk}.sha256"}:
            raise ValueError("existing release is missing assets or has an incomplete armv7 pair")

    for name, digest in actual.items():
        if digest != expected[name]:
            raise ValueError(f"existing release asset digest mismatch: {name}")


def verify_tag_ref(tag: dict, target_sha: str) -> None:
    obj = tag.get("object")
    if not isinstance(obj, dict) or obj.get("type") != "commit" or obj.get("sha") != target_sha:
        raise ValueError("existing tag does not point directly at the verified commit")


def _load_json(path: str | None) -> dict:
    if path is None:
        return json.load(sys.stdin)
    return json.loads(Path(path).read_text())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("check-drift")
    subparsers.add_parser("check-release-payload-drift")

    locate = subparsers.add_parser("locate-artifact")
    locate.add_argument("--root", required=True, type=Path)
    locate.add_argument(
        "--abi",
        choices=[DEFAULT_RELEASE_ABI, ARMV7_RELEASE_ABI],
        default=DEFAULT_RELEASE_ABI,
    )

    release = subparsers.add_parser("verify-release")
    release.add_argument("--release-json", required=True)
    release.add_argument("--target-sha", required=True)
    release.add_argument("--assets", required=True, type=Path)
    release.add_argument("--allow-missing-armv7", action="store_true")

    tag = subparsers.add_parser("verify-tag")
    tag.add_argument("--tag-json", required=True)
    tag.add_argument("--target-sha", required=True)

    args = parser.parse_args()
    try:
        if args.command == "check-drift":
            unsafe = unsafe_main_drift(_load_json(None))
            if unsafe:
                raise ValueError("non-documentation or unbounded main drift: " + ", ".join(unsafe))
        elif args.command == "check-release-payload-drift":
            unsafe = unsafe_release_payload_drift(_load_json(None))
            if unsafe:
                raise ValueError("release-payload drift: " + ", ".join(unsafe))
        elif args.command == "locate-artifact":
            selected = locate_release_artifact(args.root, args.abi)
            print(json.dumps({name: str(path) for name, path in selected.items()}))
        elif args.command == "verify-release":
            verify_existing_release(
                _load_json(args.release_json),
                args.target_sha,
                args.assets,
                allow_missing_armv7=args.allow_missing_armv7,
            )
        else:
            verify_tag_ref(_load_json(args.tag_json), args.target_sha)
    except (OSError, json.JSONDecodeError, ValueError) as error:
        print(f"::error::{error}", file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
