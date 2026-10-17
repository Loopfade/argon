#!/usr/bin/env python3
"""Check Argon's patches against the pinned upstream sources.

This small integration check downloads the Chromium files needed to validate
the CA/JNI overlay, About screen, omnibox icon dependency and M154/M155
extension-install dialog regression, applies the relevant Vanadium patches,
and applies Argon's overlay twice. A small GN project evaluates the patched
locale configuration. This does not replace full Chromium GN generation,
Java/C++ compilation, or Android smoke tests.
"""
import concurrent.futures
import fnmatch
import io
import json
from pathlib import Path
import platform
import re
import shlex
import ssl
import subprocess
import sys
import tempfile
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import apply_scoped_ca as patch
import apply_background_playback as background

SEARCHBOX_PATH = Path("components/omnibox/browser/searchbox_utils.cc")
BASE_PATHS = (patch.PROFILE, patch.VERIFIER, patch.GN,
              patch.CHROME_NET_GN, patch.CHROME_ANDROID_GN,
              patch.MAIN_PREFERENCES, patch.ABOUT_PREFERENCES, patch.ABOUT_SETTINGS,
              patch.OMNIBOX_GN, SEARCHBOX_PATH)
TRANSLATION_PATHS = tuple(dict.fromkeys(
    Path(group[key]) for group in patch.russian_translation_groups()
    for key in ("source", "catalog")
))
TRANSLATION_PATHS += tuple(dict.fromkeys(
    Path(path) for bundle in patch.russian_resource_bundles()
    for path in (*bundle["sources"], bundle["catalog"])
    if Path(path) not in TRANSLATION_PATHS
))
BASE_PATHS += tuple(path for path in TRANSLATION_PATHS
                    if not str(path).startswith("titanium/"))
BASE_PATHS += tuple(path for path in background.INPUT_PATHS
                    if not str(path).startswith("titanium/") and path not in BASE_PATHS)
EXTENSION_PATHS = (
    patch.TITANIUM_CHROME_JAVA_SOURCES, patch.TITANIUM_CHROME_RESOURCES,
    patch.TITANIUM_ANDROID_CC_SOURCES, patch.TITANIUM_ANDROID_CC_DEPS,
    patch.TITANIUM_PRIVACY_PREFERENCES,
) + tuple(path for path in TRANSLATION_PATHS
          if str(path).startswith("titanium/"))

DIALOG_PATH = Path(
    "chrome/browser/ui/android/extensions/extension_install_dialog_view_android.cc"
)


def download(url):
    with urllib.request.urlopen(url, timeout=60) as response:
        return response.read().decode("utf-8")


def vanadium_patch_exclusions(build_script):
    # Use the build's actual selection so excluded config initialization hooks
    # cannot make this integration fixture differ from the prepared source.
    selection = re.search(
        r"for pattern in (.*?); do\s+find \.build/vanadium-patches",
        build_script, re.DOTALL,
    )
    if not selection:
        raise ValueError("Vanadium patch selection not found in prepare_chromium.sh")
    return shlex.split(selection[1].replace("\\\n", " "))


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

    # The retired sed range has no end anchor in either reviewed version.
    # M155 adds two headers and uses OpenURLParams::CreateBrowserInitiated;
    # it keeps the parent-window fix and must also remain byte-for-byte intact.
    if (len(lines), old_range_start + 1) not in ((203, 57), (206, 59)):
        raise ValueError(
            "Pinned dialog source changed; review the retired M154 patch regression"
        )

    script = (ROOT / "patch.sh").read_text()
    if "DCHECK(view_android);/{/GetParentWebContents/!d" in script:
        raise ValueError("Obsolete truncating extension-dialog patch was reintroduced")
    if "DIALOG_SOURCE_SHA256_BEFORE" not in script:
        raise ValueError("Extension-dialog integrity guard is missing from patch.sh")


def verify_omnibox_icon_dependency(build_file, searchbox_source):
    # This include has nogncheck, so successful GN generation alone does not
    # catch a missing edge to its header generator before Android compilation.
    if '#include "components/omnibox/browser/vector_icons.h"' not in searchbox_source:
        raise ValueError("Omnibox icon include moved; review the build fix")
    browser = re.search(
        r'^static_library\("browser"\) \{\n(.*?)^\}',
        build_file, re.MULTILINE | re.DOTALL,
    )
    if not browser or '"searchbox_utils.cc"' not in browser[1]:
        raise ValueError("Omnibox browser target moved; review the build fix")
    required = '  if (!is_ios) {\n    deps += [ ":vector_icons" ]\n  }'
    if required not in browser[1]:
        raise ValueError("Omnibox vector icons must be dependencies on Android without VR")


def obtain_pinned_gn(directory, deps_text):
    version = re.search(r"'gn_version':\s*'git_revision:([0-9a-f]{40})'", deps_text)
    if not version:
        raise ValueError("Pinned Chromium GN version not found in DEPS")
    system = {"Linux": "linux", "Darwin": "mac", "Windows": "windows"}.get(platform.system())
    arch = {"x86_64": "amd64", "AMD64": "amd64", "aarch64": "arm64",
            "arm64": "arm64"}.get(platform.machine())
    if not system or not arch:
        raise ValueError("Unsupported host for the pinned GN locale check")
    executable = "gn.exe" if system == "windows" else "gn"
    url = (f"https://chrome-infra-packages.appspot.com/dl/gn/gn/{system}-{arch}/+/"
           + "git_revision:" + version[1])
    with urllib.request.urlopen(url, timeout=60) as response:
        with zipfile.ZipFile(io.BytesIO(response.read())) as package:
            binary = package.read(executable)
    target = directory / executable
    target.write_bytes(binary)
    target.chmod(0o755)
    return target


def verify_locale_configuration(source, gn):
    # Execute real GN list semantics; text/anchor checks missed the nonempty
    # list replacement that broke prepared-image run 39062207207.
    with tempfile.TemporaryDirectory(prefix="argon-locale-gn-") as directory:
        root = Path(directory)
        (root / ".gn").write_text('buildconfig = "//BUILDCONFIG.gn"\n')
        (root / "BUILDCONFIG.gn").write_text('''declare_args() {
  locale_test_android = true
  locale_test_official = true
}
is_android = locale_test_android
is_official_build = locale_test_official
is_ios = false
is_chromeos = false
is_apple = false
set_default_toolchain("//:default")
''')
        (root / "locales.gni").write_text(source)
        (root / "BUILD.gn").write_text('''import("//locales.gni")
if (is_android) {
  assert(platform_pak_locales == [ "en-US", "ru" ])
  assert(locales_without_pseudolocales == [ "en-US", "ru" ])
  assert(locales_with_pseudolocales == [ "en-US", "ru" ])
  assert(android_bundle_locales_as_resources == [ "ru" ])
  assert(extended_locales == [])
  assert(pseudolocales == [])
} else {
  assert(filter_include(platform_pak_locales, [ "de" ]) == [ "de" ])
}
assert(filter_include(xtb_locales, [ "en-XA", "ar-XB" ]) == [])
assert(filter_include(all_chrome_locales, [ "de", "ru" ]) == [ "de", "ru" ])
group("locale_check") {
}
toolchain("default") {
  tool("stamp") {
    command = "true"
  }
}
''')
        for android in (True, False):
            for official in (True, False):
                args = ("locale_test_android=" + str(android).lower()
                        + " locale_test_official=" + str(official).lower())
                output = root / "out"
                output.mkdir(exist_ok=True)
                (output / "args.gn").write_text(args + "\n")
                # desc evaluates the GN sources without compiling anything;
                # it only needs this marker to recognize the output directory.
                (output / "build.ninja").write_text("# Locale evaluation only.\n")
                result = subprocess.run(
                    [str(gn), "desc", "out", "//:locale_check", "--format=json"], cwd=root,
                    text=True, capture_output=True,
                )
                if result.returncode:
                    raise ValueError("Locale GN check failed (" + args + "):\n"
                                     + result.stdout + result.stderr)


def main():
    lock = json.loads((ROOT / "build-lock.json").read_text())
    for key in ("chromium_commit", "boringssl_commit", "titanium_commit", "vanadium_commit"):
        if not re.fullmatch(r"[0-9a-f]{40}", lock[key]):
            raise ValueError(f"Invalid commit pin: {key}")
    chromium = "https://raw.githubusercontent.com/chromium/chromium/" + lock["chromium_commit"]
    upstream_paths = (*BASE_PATHS, DIALOG_PATH)
    urls = {str(path): f"{chromium}/{path}" for path in upstream_paths}
    urls.update({"chrome/VERSION": chromium + "/chrome/VERSION", "DEPS": chromium + "/DEPS",
                 "titanium-tree": "https://api.github.com/repos/jqssun/android-titanium-browser/git/trees/"
                 + lock["titanium_commit"]})
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
        values = dict(zip(urls, executor.map(download, urls.values())))
    verify_pins(lock, values["chrome/VERSION"], values["DEPS"],
                json.loads(values["titanium-tree"]))

    with tempfile.TemporaryDirectory(prefix="argon-upstream-") as temporary:
        src = Path(temporary)
        for path in upstream_paths:
            target = src / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(values[str(path)])
        subprocess.run(["git", "init", "-q", str(src)], check=True)
        paths = (*BASE_PATHS, *EXTENSION_PATHS, DIALOG_PATH)
        includes = [f"--include={path}" for path in paths]
        count = 0
        exclusions = vanadium_patch_exclusions((ROOT / "scripts/prepare_chromium.sh").read_text())
        for source in sorted((ROOT / "vanadium/patches").glob("*.patch")):
            if any(fnmatch.fnmatchcase(source.name, pattern)
                   for pattern in exclusions):
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
        # Validate the dialog after Vanadium patches, matching build.sh ordering:
        # Chromium checkout -> Vanadium patches -> Argon patch.sh.
        verify_extension_install_dialog((src / DIALOG_PATH).read_text())
        patch.apply(src, product_name=False)
        background.apply(src)
        verify_omnibox_icon_dependency(
            (src / patch.OMNIBOX_GN).read_text(),
            (src / SEARCHBOX_PATH).read_text(),
        )
        # Android details must describe the same pinned root used by the verifier.
        resource_pem = (src / patch.TITANIUM_ROOT_RESOURCE).read_text("ascii")
        if ssl.PEM_cert_to_DER_cert(resource_pem) != patch.load_root():
            raise ValueError("Android certificate details differ from the trusted root")
        resources = re.findall(
            r'"(java/res/[^"\n]*argon[^"\n]*)"',
            (src / patch.TITANIUM_CHROME_RESOURCES).read_text(),
        )
        if len(resources) != len(set(resources)):
            raise ValueError("Duplicate Argon Android resource registration")
        for resource in resources:
            if not (src / "titanium/chromium_src/chrome/android" / resource).is_file():
                raise ValueError(f"Missing Argon Android resource: {resource}")
        before = {path: path.read_bytes() for path in src.rglob("*")
                  if path.is_file() and ".git" not in path.relative_to(src).parts}
        patch.apply(src, product_name=False)
        background.apply(src)
        after = {path: path.read_bytes() for path in src.rglob("*")
                 if path.is_file() and ".git" not in path.relative_to(src).parts}
        if before != after:
            raise ValueError("Argon patch is not idempotent on the pinned upstream")
        gn = obtain_pinned_gn(src, values["DEPS"])
        verify_locale_configuration((src / background.LOCALES).read_text(), gn)
    print(f'PASS: Chromium {lock["chromium_version"]}, dependency pins, '
          f'{count} relevant Vanadium patches, omnibox icon dependency '
          'and idempotent Argon CA/JNI/About/Russian translation/background playback '
          'patches; pinned GN locale checks (Android/desktop, official/development)')


if __name__ == "__main__":
    main()
