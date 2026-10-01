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
    "BUILDING.md",
}
DOCUMENTATION_PREFIXES = ("docs/",)
GITHUB_COMPARE_FILE_LIMIT = 300
RELEASE_APK_GLOB = "Argon-*-release-arm64-v8a.apk"
RELEASE_LICENSES = (
    "Chromium-LICENSE.txt",
    "Ruthenium-LICENSE.txt",
    "Titanium-LICENSE.txt",
)


def is_documentation_path(path: str) -> bool:
    return path in DOCUMENTATION_FILES or path.startswith(DOCUMENTATION_PREFIXES)


def unsafe_main_drift(compare: dict) -> list[str]:
    """Return changed paths that make an older build stale.

    GitHub's compare endpoint reports at most 300 files. Exactly hitting that
    limit is treated as unsafe because additional non-documentation files may
    have been omitted.
    """
    status = compare.get("status")
    if status == "identical":
        return []
    if status != "ahead":
        return [f"<compare-status:{status}>"]

    files = compare.get("files")
    if not isinstance(files, list):
        return ["<missing-files>"]
    if len(files) >= GITHUB_COMPARE_FILE_LIMIT:
        return ["<compare-file-limit>"]

    paths = []
    for item in files:
        if not isinstance(item, dict) or not isinstance(item.get("filename"), str):
            return ["<invalid-file-entry>"]
        paths.append(item["filename"])
    return [path for path in paths if not is_documentation_path(path)]


def locate_release_artifact(root: Path) -> dict[str, Path]:
    """Locate exactly one verified arm64 release payload below an artifact root.

    actions/upload-artifact preserves uploaded directory layout, so release
    files may live in a nested ABI directory such as arm64-v8a/.
    """
    candidates = sorted(
        path for path in root.rglob(RELEASE_APK_GLOB) if path.is_file()
    )
    if len(candidates) != 1:
        raise ValueError(
            f"expected exactly one release arm64 APK, found {len(candidates)}"
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


def verify_existing_release(release: dict, target_sha: str, assets: Path) -> None:
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

    if set(actual) != set(expected):
        raise ValueError("existing release asset names do not match the verified asset set")
    for name, digest in expected.items():
        if actual[name] != digest:
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

    locate = subparsers.add_parser("locate-artifact")
    locate.add_argument("--root", required=True, type=Path)

    release = subparsers.add_parser("verify-release")
    release.add_argument("--release-json", required=True)
    release.add_argument("--target-sha", required=True)
    release.add_argument("--assets", required=True, type=Path)

    tag = subparsers.add_parser("verify-tag")
    tag.add_argument("--tag-json", required=True)
    tag.add_argument("--target-sha", required=True)

    args = parser.parse_args()
    try:
        if args.command == "check-drift":
            unsafe = unsafe_main_drift(_load_json(None))
            if unsafe:
                raise ValueError("non-documentation or unbounded main drift: " + ", ".join(unsafe))
        elif args.command == "locate-artifact":
            selected = locate_release_artifact(args.root)
            print(json.dumps({name: str(path) for name, path in selected.items()}))
        elif args.command == "verify-release":
            verify_existing_release(
                _load_json(args.release_json), args.target_sha, args.assets
            )
        else:
            verify_tag_ref(_load_json(args.tag_json), args.target_sha)
    except (OSError, json.JSONDecodeError, ValueError) as error:
        print(f"::error::{error}", file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
