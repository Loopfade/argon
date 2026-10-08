#!/usr/bin/env python3
"""Retain source-image digests while any consumer or publisher is active."""
import argparse
import json
import os
from pathlib import Path
import subprocess


def pages(path):
    return json.loads(subprocess.check_output(
        ["gh", "api", path, "--paginate", "--slurp"], text=True))


def active_runs(runs, current_run=None):
    return [run for run in runs if run["status"] != "completed"
            and str(run["id"]) != str(current_run)]


def publication_active(repository, current_run):
    result = pages(f"repos/{repository}/actions/workflows/build-chromium-image.yml/runs?branch=main&per_page=100")
    return bool(active_runs([run for page in result for run in page["workflow_runs"]], current_run))


def consumers_active(repository):
    for workflow in ("build.yml", "validate.yml"):
        result = pages(f"repos/{repository}/actions/workflows/{workflow}/runs?per_page=100")
        if active_runs([run for page in result for run in page["workflow_runs"]]):
            return True
    return False


def retained_version(versions, tag):
    tagged = [entry for entry in versions
              if tag in entry.get("metadata", {}).get("container", {}).get("tags", [])]
    if len(tagged) != 1:
        raise ValueError(f"Expected exactly one published {tag} version, found {len(tagged)}")
    return tagged[0]["id"]


def prune(repository, current_run, package_path, tag):
    def busy():
        return publication_active(repository, current_run) or consumers_active(repository)

    if busy():
        print("::notice::Argon build or image publication is active; retaining previous digests.")
        return
    versions = [entry for page in pages(package_path + "?per_page=100") for entry in page]
    keep = retained_version(versions, tag)
    for entry in versions:
        if entry["id"] == keep:
            continue
        # Recheck before each destructive API call. API failures stop cleanup.
        if busy():
            print("::notice::A build started during cleanup; retaining remaining digests.")
            return
        current = [item for page in pages(package_path + "?per_page=100") for item in page]
        if retained_version(current, tag) != keep:
            print("::notice::The moving image tag changed; deferring cleanup.")
            return
        subprocess.run(["gh", "api", "--method", "DELETE", f"{package_path}/{entry['id']}"], check=True)
    print("Superseded prepared-image versions removed.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check-publication", action="store_true")
    args = parser.parse_args()
    repository = os.environ["GH_REPO"]
    current_run = os.environ["CURRENT_RUN_ID"]
    if args.check_publication:
        safe = not publication_active(repository, current_run)
        with Path(os.environ["GITHUB_OUTPUT"]).open("a") as stream:
            stream.write(f"apply={str(safe).lower()}\n")
        return
    owner, package = os.environ["OWNER"], os.environ["PACKAGE"]
    prune(repository, current_run, f"/users/{owner}/packages/container/{package}/versions", os.environ["KEEP_TAG"])


if __name__ == "__main__":
    main()
