#!/usr/bin/env python3
"""Check Argon's patches against the pinned upstream sources.

This small integration check downloads the Chromium files needed to validate
the CA/JNI overlay and the M154 extension-install dialog regression, applies
the relevant Vanadium patches, and applies Argon's overlay twice. It does not
replace GN, Java/C++ compilation, or Android smoke tests.
"""
import concurrent.futures
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import apply_scoped_ca as patch

BASE_PATHS = (patch.PROFILE, patch.VERIFIER, patch.GN,
              patch.CHROME_NET_GN, patch.CHROME_ANDROID_GN)
EXTENSION_PATHS = (
    patch.TITANIUM_CHROME_JAVA_SOURCES, patch.TITANIUM_CHROME_RESOURCES,
    patch.TITANIUM_ANDROID_CC_SOURCES, patch.TITANIUM_ANDROID_CC_DEPS,
    patch.TITANIUM_PRIVACY_PREFERENCES,
)

DIALOG_PATH = Path(
    "chrome/browser/ui/android/extensions/extension_install_dialog_view_android.cc"
)


def download(url):
    with urllib.request.urlopen(url, timeout=60) as response:
        return response.read().decode("utf-8")


def verify_pins(lock, version_text, deps_text, titanium_tree):
    version = dict(line.split("=", 1) for line in version_text.splitlines() if "=" in line)
    actual = ".".join(version[key] for key in ("MAJOR", "MINOR", "BUILD", "PATCH"))
    if actual != lock["chromium_version"]:
        raise ValueError("Chromium commit does not match the locked version")
    boringssl = re.search(r"'boringssl_revision':\s*'([0-9a-f]{40})'", deps_text)
    if not boringssl or boringssl[1] != lock["boringssl_commit"]:
        raise ValueError("BoringSSL pin does not match Chromium DEPS")
    vanadium = [entry for entry in titanium_tree["tree"]
                if entry["path"] == "vanadium" and entry["type"] == "commit"]
    if len(vanadium) != 1 or vanadium[0]["sha"] != lock["vanadium_commit"]:
        raise ValueError("Vanadium pin does not match the Titanium commit")


def verify_extension_install_dialog(source):
    lines = source.splitlines()
    parent_window = (
        "  ui::WindowAndroid* window_android = show_params->GetParentWindow();"
    )
    web_contents = (
        "  content::WebContents* web_contents = "
        "show_params->GetParentWebContents();"
    )
    if parent_window not in lines:
        raise ValueError("Pinned Chromium no longer contains the reviewed M154 dialog fix")
    try:
        old_range_start = lines.index(web_contents)
    except ValueError as exc:
        raise ValueError("Pinned Chromium dialog source moved the old patch anchor") from exc
    if any("DCHECK(view_android);" in line for line in lines[old_range_start:]):
        raise ValueError("Pinned Chromium unexpectedly restored the retired end anchor")

    # Regression proof: on Chromium 154 the obsolete sed range starts on line
    # 57 and never finds its old end anchor, so it deletes lines 58..203.
    if len(lines) != 203 or old_range_start + 1 != 57:
        raise ValueError(
            "Pinned dialog source changed; review the retired M154 patch regression"
        )

    script = (ROOT / "patch.sh").read_text()
    if "DCHECK(view_android);/{/GetParentWebContents/!d" in script:
        raise ValueError("Obsolete truncating extension-dialog patch was reintroduced")
    if "DIALOG_SOURCE_SHA256_BEFORE" not in script:
        raise ValueError("Extension-dialog integrity guard is missing from patch.sh")


def main():
    lock = json.loads((ROOT / "build-lock.json").read_text())
    for key in ("chromium_commit", "boringssl_commit", "titanium_commit", "vanadium_commit"):
        if not re.fullmatch(r"[0-9a-f]{40}", lock[key]):
            raise ValueError(f"Invalid commit pin: {key}")
    chromium = "https://raw.githubusercontent.com/chromium/chromium/" + lock["chromium_commit"]
    urls = {str(path): f"{chromium}/{path}" for path in BASE_PATHS}
    urls[str(DIALOG_PATH)] = f"{chromium}/{DIALOG_PATH}"
    urls.update({"chrome/VERSION": chromium + "/chrome/VERSION", "DEPS": chromium + "/DEPS",
                 "titanium-tree": "https://api.github.com/repos/jqssun/android-titanium-browser/git/trees/"
                 + lock["titanium_commit"]})
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
        values = dict(zip(urls, executor.map(download, urls.values())))
    verify_pins(lock, values["chrome/VERSION"], values["DEPS"],
                json.loads(values["titanium-tree"]))
    verify_extension_install_dialog(values[str(DIALOG_PATH)])

    with tempfile.TemporaryDirectory(prefix="argon-upstream-") as temporary:
        src = Path(temporary)
        for path in BASE_PATHS:
            target = src / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(values[str(path)])
        subprocess.run(["git", "init", "-q", str(src)], check=True)
        paths = (*BASE_PATHS, *EXTENSION_PATHS)
        includes = [f"--include={path}" for path in paths]
        count = 0
        for source in sorted((ROOT / "vanadium/patches").glob("*.patch")):
            # Titanium excludes Trichrome. Its browser-target patch is the only
            # excluded patch touching this CA/JNI subset of Chromium sources.
            if "trichrome" in source.name:
                continue
            data = source.read_text().replace("VANADIUM", "TITANIUM")
            data = data.replace("Vanadium", "Titanium").replace("vanadium", "titanium")
            if not any(f"diff --git a/{path} b/{path}" in data for path in paths):
                continue
            result = subprocess.run(["git", "apply", "--whitespace=nowarn", *includes],
                                    cwd=src, input=data, text=True, capture_output=True)
            if result.returncode:
                raise ValueError(f"{source.name}: {result.stderr}")
            count += 1
        patch.apply(src, product_name=False)
        before = {path: path.read_bytes() for path in src.rglob("*")
                  if path.is_file() and ".git" not in path.relative_to(src).parts}
        patch.apply(src, product_name=False)
        after = {path: path.read_bytes() for path in src.rglob("*")
                 if path.is_file() and ".git" not in path.relative_to(src).parts}
        if before != after:
            raise ValueError("Argon patch is not idempotent on the pinned upstream")
    print(f'PASS: Chromium {lock["chromium_version"]}, dependency pins, '
          f'{count} relevant Vanadium patches and idempotent Argon CA/JNI overlay')


if __name__ == "__main__":
    main()
