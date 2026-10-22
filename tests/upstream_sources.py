"""Fetch exact Chromium files from the canonical source used by the build."""
import base64
import os
from pathlib import PurePosixPath
import re
import time
import urllib.error
import urllib.parse
import urllib.request

CHROMIUM_SOURCE = "https://chromium.googlesource.com/chromium/src/+/"
TRANSIENT_HTTP_ERRORS = {429, 500, 502, 503, 504}


def chromium_url(revision, path):
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("Chromium sources require an immutable commit SHA")
    path = PurePosixPath(path)
    if path.is_absolute() or ".." in path.parts or str(path) == ".":
        raise ValueError("Expected a relative Chromium source path")
    return CHROMIUM_SOURCE + revision + "/" + urllib.parse.quote(str(path)) + "?format=TEXT"


def download(url):
    request = url
    token = os.environ.get("GH_TOKEN")
    if token and url.startswith("https://api.github.com/"):
        request = urllib.request.Request(url, headers={
            "Authorization": "Bearer " + token,
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        })
    for attempt in range(3):
        delay = 2 ** attempt
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                data = response.read()
            break
        except urllib.error.HTTPError as error:
            if error.code not in TRANSIENT_HTTP_ERRORS or attempt == 2:
                error.add_note("Upstream source URL: " + url)
                raise
            retry_after = (error.headers or {}).get("Retry-After", "")
            if retry_after.isdecimal():
                delay = max(delay, min(int(retry_after), 60))
        except (urllib.error.URLError, TimeoutError) as error:
            if attempt == 2:
                error.add_note("Upstream source URL: " + url)
                raise
        time.sleep(delay)
    if url.startswith(CHROMIUM_SOURCE) and url.endswith("?format=TEXT"):
        data = base64.b64decode(data.strip(), validate=True)
    return data.decode("utf-8")
