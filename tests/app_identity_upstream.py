"""Check package-derived Android identifiers against the pinned production sources."""
from pathlib import Path
import os
import re
import tempfile
from unittest.mock import patch

import build_identity

MANIFEST = build_identity.MANIFEST
FILE_PROVIDER = Path("chrome/browser/util/android/java/src/org/chromium/chrome/browser/util/ChromeFileProvider.java")
GCM = Path("components/gcm_driver/android/java/src/org/chromium/components/gcm_driver/GoogleCloudMessagingV2.java")
NOTIFICATIONS = Path("components/browser_ui/notifications/android/java/src/org/chromium/components/browser_ui/notifications/NotificationManagerProxyImpl.java")
APK_TEMPLATE = Path("chrome/android/chrome_public_apk_tmpl.gni")
CONFIG = Path("build/config/android/config.gni")
INPUT_PATHS = (MANIFEST, FILE_PROVIDER, GCM, NOTIFICATIONS, APK_TEMPLATE, CONFIG)


def verify_upstream(src):
    sources = {path: (src / path).read_text() for path in INPUT_PATHS}
    manifest = sources[MANIFEST]
    authorities = re.findall(r'android:authorities="([^"]+)"', manifest)
    if len(authorities) < 2 or any(not item.startswith("{{ manifest_package }}")
                                  for value in authorities for item in value.split(";")):
        raise ValueError("Review upstream provider authority isolation")
    if 'ContextUtils.getApplicationContext().getPackageName() + AUTHORITY_SUFFIX' not in sources[FILE_PROVIDER]:
        raise ValueError("FileProvider runtime no longer uses its own package")
    if not re.search(r'PendingIntent.getBroadcast\(\s*ContextUtils.getApplicationContext\(\)', sources[GCM]):
        raise ValueError("GCM registration no longer identifies its application context")
    if 'NotificationManagerCompat.from(ContextUtils.getApplicationContext())' not in sources[NOTIFICATIONS]:
        raise ValueError("NotificationManager no longer uses its application context")
    if '"manifest_package=$_manifest_package"' not in sources[APK_TEMPLATE]:
        raise ValueError("Chromium manifest no longer receives its selected package")
    if 'android_override_version_code = ""' not in sources[CONFIG]:
        raise ValueError("Review upstream test versionCode override")
    with tempfile.TemporaryDirectory(prefix="argon-identity-upstream-") as temporary:
        root = Path(temporary)
        target = root / MANIFEST
        target.parent.mkdir(parents=True)
        target.write_text(manifest)
        resource = root / build_identity.CHANNEL_CONSTANTS
        resource.parent.mkdir(parents=True)
        resource.write_text('<resources><string name="app_name">Argon</string></resources>')
        for arch in ("arm64", "arm"):
            with patch.dict(os.environ, {"ARGON_STANDALONE_TEST": "true", "ARGON_TEST_VERSION_CODE": "1000000017"}):
                build_identity.configure(root, root / "args.gn", arch, True, "release")
            if build_identity.GOOGLE_CHROME_SCHEME in target.read_text() or '<original-package' in target.read_text():
                raise ValueError("Test manifest retains a production URI/package alias")
            with patch.dict(os.environ, {"ARGON_STANDALONE_TEST": "false"}):
                build_identity.configure(root, root / "args.gn", arch, True, "release")
            if target.read_text() != manifest:
                raise ValueError("Production manifest was not restored exactly")
    return "PASS: pinned authorities, FileProvider, push context, notifications and standalone APK variants"
