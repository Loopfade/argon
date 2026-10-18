#!/usr/bin/env python3
"""Retain source-image digests while any consumer or publisher is active."""
import argparse
import json
import os
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


def consumers_active(repository, exclude_run=None):
    # Query repository history so an in-flight legacy build still retains its
    # digest after its workflow file has been deleted from main.
    result = pages(f"repos/{repository}/actions/runs?per_page=100")
    consumers = {".github/workflows/build-all.yml", ".github/workflows/validate.yml",
                 ".github/workflows/build.yml"}
    runs = [run for page in result for run in page["workflow_runs"]
            if run.get("path") in consumers]
    return bool(active_runs(runs, exclude_run))


def retained_version(versions, tag):
    tagged = [entry for entry in versions
              if tag in entry.get("metadata", {}).get("container", {}).get("tags", [])]
    if len(tagged) != 1:
        raise ValueError(f"Expected exactly one published {tag} version, found {len(tagged)}")
    return tagged[0]["id"]


def prune(repository, current_run, package_path, tag, exclude_consumer=None):
    def busy():
        return publication_active(repository, current_run) or consumers_active(repository, exclude_consumer)

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
    parser.add_argument("--ignore-current-consumer", action="store_true",
                        help="Only use after every build/publish job in this run has finished")
    args = parser.parse_args()
    repository = os.environ["GH_REPO"]
    current_run = os.environ["CURRENT_RUN_ID"]
    owner, package = os.environ["OWNER"], os.environ["PACKAGE"]
    prune(repository, current_run, f"/users/{owner}/packages/container/{package}/versions", os.environ["KEEP_TAG"],
          exclude_consumer=current_run if args.ignore_current_consumer else None)


if __name__ == "__main__":
    main()
