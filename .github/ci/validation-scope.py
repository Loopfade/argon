#!/usr/bin/env python3
"""Select PR checks from the complete diff of the actual merge checkout."""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[2]
DOC_FILES = {"README.md", "README.upstream.md", "VALIDATION.md", ".nojekyll"}
# These files change CI/release orchestration, without changing the Chromium
# sources compiled by test-domain-policy.sh. Unknown paths always get all checks.
RUNTIME_FILES = {
    "index.html", "dashboard-data.json",
    "scripts/apk_identity.py", "scripts/release_policy.py",
    "scripts/sign_and_verify.py", "scripts/VerifyReleaseKey.java",
    ".github/ci/branch-test.py", ".github/ci/continue-build.py",
    ".github/ci/continuous-build.py", ".github/ci/run-in-build-container.sh",
    ".github/ci/prune-compiler-cache.py", ".github/ci/prune-prepared-images.py",
    ".github/ci/inspect-cached-modules.py", ".github/ci/inspect-compiler-cache.py",
    ".github/ci/update-dashboard.py",
}


def classify(paths):
    full, domain = False, False
    for path in paths:
        if path in DOC_FILES or path.startswith("docs/"):
            continue
        full = True
        runtime = (path in RUNTIME_FILES
                   or path.startswith(".github/package-metadata/")
                   or path.startswith(".github/actions/compiler-checkpoint/")
                   or (path.startswith(".github/workflows/")
                       and path != ".github/workflows/validate.yml"))
        domain |= not runtime
    return {"full": full, "domain": domain}


def changed_paths(root, base, head):
    for sha in (base, head):
        if not re.fullmatch(r"[0-9a-f]{40}", sha):
            raise ValueError("Expected pinned PR base and head commits")
    def git(*args):
        return subprocess.check_output(["git", "-C", str(root), *args])
    # checkout fetch-depth=2 includes both parents of GitHub's merge commit.
    # Never derive a skip from a diff against an unexpected/stale base.
    parents = git("show", "-s", "--format=%P", "HEAD").decode().split()
    if parents != [base, head]:
        return None
    # --no-renames includes both paths of a rename, including code -> docs.
    output = git("diff", "--no-renames", "--name-only", "-z", base, "HEAD")
    return [os.fsdecode(name) for name in output.split(b"\0") if name]


def cache_prefix(root):
    lock = json.loads((root / "build-lock.json").read_text())
    version = lock["chromium_version"]
    if not re.fullmatch(r"[0-9]+(?:\.[0-9]+){3}", version):
        raise ValueError("Invalid pinned Chromium version")
    digest = hashlib.sha256()
    for key in ("chromium_commit", "depot_tools_commit", "boringssl_commit", "vanadium_commit"):
        digest.update(key.encode() + b"\0" + lock[key].encode() + b"\0")
    for name in ("args.gn", "scripts/configure_build.py"):
        digest.update(name.encode() + b"\0" + (root / name).read_bytes())
    return f"argon-ccache-validation-v2-{version}-{digest.hexdigest()}"


def select_checks(root, event, ref, base="", head=""):
    if event in ("push", "workflow_dispatch") and ref == "refs/heads/main":
        # A trusted main run only seeds the shared compiler cache. It must not
        # repeat the required source/UI/integration checks already run in PR.
        return {"full": False, "domain": True}
    if event != "pull_request":
        raise ValueError("Cache warmup must run on main")
    paths = changed_paths(root, base, head)
    if paths is None:
        print("::notice::Родители merge-коммита отличаются от события PR; выполняются все проверки.")
        return {"full": True, "domain": True}
    return classify(paths)


def main():
    selected = select_checks(ROOT, os.environ["GITHUB_EVENT_NAME"], os.environ["GITHUB_REF"],
                             os.environ.get("PR_BASE_SHA", ""), os.environ.get("PR_HEAD_SHA", ""))
    prefix = cache_prefix(ROOT)
    with open(os.environ["GITHUB_OUTPUT"], "a") as output:
        for key, value in selected.items():
            output.write(f"{key}={str(value).lower()}\n")
        output.write(f"cache-prefix={prefix}\n")
    print("Выбранные проверки: " + json.dumps(selected, ensure_ascii=False))


if __name__ == "__main__":
    main()
