#!/usr/bin/env python3
"""Run Ninja once, stream its log, and checkpoint a separate ccache snapshot."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time


def paths():
    arch = os.environ["TARGET_ARCH"]
    if arch not in ("arm64", "arm"):
        raise ValueError("Unsupported build architecture")
    root = Path(".build")
    root.mkdir(exist_ok=True)
    return root / f"continuous-{arch}.log", root / f"continuous-{arch}.json"


def atomic_result(path, status, exhausted=False):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps({"status": status, "budget_exhausted": exhausted}))
    temporary.replace(path)


def worker():
    _log, result = paths()
    started = time.time()
    remaining = (int(os.environ["BUILD_JOB_STARTED_AT"])
                 + int(os.environ["BUILD_JOB_BUDGET_SECONDS"])
                 - int(os.environ["BUILD_FINALIZE_RESERVE_SECONDS"]) - 180 - int(started))
    try:
        if remaining < 60:
            atomic_result(result, 124, True)
            return
        env = {**os.environ, "BUILD_MODE": "finish", "BUILD_EXEC_TIMEOUT_SECONDS": str(remaining),
               "BUILD_JOBS": str(os.cpu_count() or 4)}
        status = subprocess.call(["bash", ".github/ci/run-in-build-container.sh"], env=env)
        elapsed = time.time() - started
        exhausted = status == 124 or (status == 137 and elapsed >= remaining)
        atomic_result(result, status, exhausted)
    except Exception:
        atomic_result(result, 1)
        raise


def start():
    log, result = paths()
    if result.exists():
        raise ValueError("A continuous build result already exists")
    with log.open("wb") as output:
        child = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "worker"],
                                 stdout=output, stderr=subprocess.STDOUT, start_new_session=True)
    result.with_suffix(".pid").write_text(str(child.pid))
    print(f"Запущена непрерывная сборка (PID {child.pid}); контрольные точки не останавливают Ninja.")


def stream_log(log):
    offset_file = log.with_suffix(".offset")
    offset = int(offset_file.read_text()) if offset_file.exists() else 0
    if log.exists():
        with log.open("rb") as source:
            source.seek(offset)
            shutil.copyfileobj(source, sys.stdout.buffer)
            offset_file.write_text(str(source.tell()))
        sys.stdout.flush()


def wait(minutes):
    log, result = paths()
    deadline = time.monotonic() + minutes * 60
    while not result.exists() and time.monotonic() < deadline:
        stream_log(log)
        pid = int(result.with_suffix(".pid").read_text())
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            atomic_result(result, 1)
            break
        time.sleep(min(5, max(0, deadline - time.monotonic())))
    stream_log(log)
    with open(os.environ["GITHUB_OUTPUT"], "a") as output:
        output.write(f"done={str(result.exists()).lower()}\n")


def snapshot(source, destination):
    """Copy immutable cache entries while compilation keeps using the live cache.

    ccache installs complete entries atomically. Evictions during the copy may
    omit entries, which is a safe cache miss on restoration. Never archive the
    directory being concurrently written by compiler processes.
    """
    source, destination = Path(source), Path(destination)
    if source.resolve() == destination.resolve():
        raise ValueError("The live cache and saved snapshot must be separate")
    destination.mkdir(parents=True, exist_ok=True)
    seen = set()
    for parent, directories, files in os.walk(source):
        directories[:] = [name for name in directories if name != "tmp"
                          and not (Path(parent) / name).is_symlink()]
        for name in files:
            path = Path(parent) / name
            if path.is_symlink() or ".tmp." in name:
                continue
            relative = path.relative_to(source)
            target = destination / relative
            seen.add(relative)
            try:
                stat = path.stat()
                if target.exists() and (target.stat().st_size, target.stat().st_mtime_ns) == (stat.st_size, stat.st_mtime_ns):
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                temporary = target.with_name(target.name + ".snapshot-tmp")
                try:
                    shutil.copy2(path, temporary)
                    temporary.replace(target)
                finally:
                    temporary.unlink(missing_ok=True)
            except FileNotFoundError:
                continue
    for path in destination.rglob("*"):
        if path.is_file() and path.relative_to(destination) not in seen:
            path.unlink(missing_ok=True)


def finish():
    log, result = paths()
    stream_log(log)
    if not result.exists():
        raise ValueError("Continuous build has not finished")
    data = json.loads(result.read_text())
    if data["budget_exhausted"]:
        Path(f".build/cache-warm-budget-exhausted-{os.environ['TARGET_ARCH']}").touch()
    return data["status"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("start", "worker", "wait", "snapshot", "finish"))
    parser.add_argument("--minutes", type=float, default=60)
    parser.add_argument("--source")
    parser.add_argument("--destination")
    args = parser.parse_args()
    if args.action == "snapshot":
        snapshot(args.source, args.destination)
    elif args.action == "wait":
        wait(args.minutes)
    else:
        return {"start": start, "worker": worker, "finish": finish}[args.action]()


if __name__ == "__main__":
    sys.exit(main())
