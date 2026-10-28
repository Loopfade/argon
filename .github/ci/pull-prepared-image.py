#!/usr/bin/env python3
"""Pull the selected immutable image, retrying bounded GHCR rate limits."""
import argparse
from collections import deque
import re
import subprocess
import sys
import time


MAX_ATTEMPTS = 4
IMAGE = re.compile(r"ghcr\.io/[a-z0-9_.-]+/argon-build@sha256:[a-f0-9]{64}")


def rate_limited(message):
    message = message.lower()
    return ("toomanyrequests" in message or "too many requests" in message
            or re.search(r"\b(?:http(?: status)?|status(?: code)?)\s*:?\s*429\b", message) is not None)


def retry_delay(message, attempt):
    delay = 10 * 2 ** (attempt - 1)
    match = re.search(r"retry-after:\s*(\d+(?:\.\d+)?)(ms|s)?\b", message, re.I)
    if match:
        seconds = float(match[1]) / (1000 if (match[2] or "").lower() == "ms" else 1)
        delay = max(delay, min(seconds, 60))
    return delay


def pull(image):
    if not IMAGE.fullmatch(image):
        raise ValueError("Expected the selected GHCR argon-build image with an immutable SHA-256 digest")
    command = ["docker", "pull", image]
    for attempt in range(1, MAX_ATTEMPTS + 1):
        tail = deque(maxlen=80)
        with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              text=True, errors="replace") as process:
            for line in process.stdout:
                print(line, end="", flush=True)
                tail.append(line)
            status = process.wait()
        if status == 0:
            return
        message = "".join(tail)
        if attempt == MAX_ATTEMPTS or not rate_limited(message):
            raise subprocess.CalledProcessError(status, command)
        delay = retry_delay(message, attempt)
        print(f"::warning::GHCR ограничил скачивание образа; попытка {attempt + 1}/{MAX_ATTEMPTS} "
              f"через {delay:g} с с тем же digest.", flush=True)
        time.sleep(delay)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image")
    args = parser.parse_args()
    try:
        pull(args.image)
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        print(f"::error::Не удалось скачать подготовленный образ: {error}", file=sys.stderr)
        return max(1, getattr(error, "returncode", 1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
