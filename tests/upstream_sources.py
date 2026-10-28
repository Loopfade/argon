"""Fetch exact Chromium commits from Gitiles or the official GitHub mirror."""
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


def chromium_mirror_url(url):
    if not url.startswith(CHROMIUM_SOURCE):
        return None
    match = re.fullmatch(r"([0-9a-f]{40})/(.+)\?format=TEXT", url[len(CHROMIUM_SOURCE):])
    if not match:
        raise ValueError("Chromium mirrors require an immutable commit and file path")
    revision, encoded_path = match.groups()
    path = urllib.parse.unquote(encoded_path)
    # Apply the same path and revision validation as the canonical origin.
    chromium_url(revision, path)
    return ("https://raw.githubusercontent.com/chromium/chromium/" + revision
            + "/" + urllib.parse.quote(str(PurePosixPath(path))))


def download(url, *, prefer_mirror=False):
    mirror = chromium_mirror_url(url) if prefer_mirror else None
    if not mirror:
        return _download(url)
    try:
        return _download(mirror)
    except urllib.error.HTTPError as error:
        if error.code not in TRANSIENT_HTTP_ERRORS | {404}:
            raise
    except (urllib.error.URLError, TimeoutError):
        pass
    print("::warning::Зеркало Chromium недоступно; используется Gitiles для того же SHA: "
          + url, flush=True)
    return _download(url)


def _download(url):
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
            elif error.code == 429:
                delay = 30
        except (urllib.error.URLError, TimeoutError) as error:
            if attempt == 2:
                error.add_note("Upstream source URL: " + url)
                raise
        time.sleep(delay)
    if url.startswith(CHROMIUM_SOURCE) and url.endswith("?format=TEXT"):
        data = base64.b64decode(data.strip(), validate=True)
    return data.decode("utf-8")
