#!/usr/bin/env python3
"""Audit the merged APK manifest as decoded by the SDK's aapt2 xmltree."""
import re
import xml.etree.ElementTree as ET

ANDROID_URI = "http://schemas.android.com/apk/res/android"
ANDROID = "{" + ANDROID_URI + "}"
WEB_SCHEMES = {"http", "https", "about", "content", "file", "package"}


def manifest_from_dump(text):
    """Read aapt2's hierarchy, including string and typed integer attributes."""
    stack = []
    root = None
    for line in text.splitlines():
        element = re.match(r"^(\s*)E: ([\w.:-]+)(?:\s|$)", line)
        if element:
            indent = len(element[1])
            while stack and stack[-1][0] >= indent:
                stack.pop()
            node = ET.Element(element[2])
            if stack:
                stack[-1][1].append(node)
            elif root is not None:
                raise ValueError("Multiple manifest roots")
            else:
                root = node
            stack.append((indent, node))
            continue
        attribute = re.match(r"^\s*A: ([^\s=()]+)(?:\(0x[0-9a-fA-F]+\))?=(.*)$", line)
        if not attribute:
            continue
        if not stack:
            raise ValueError("Manifest attribute without an element")
        name, value = attribute.groups()
        # Current aapt2's XmlPrinter writes namespace URIs, not the declared
        # prefix. Keep the older android: spelling supported as well.
        if name.startswith(ANDROID_URI + ":"):
            name = ANDROID + name[len(ANDROID_URI) + 1:]
        elif name.startswith("android:"):
            name = ANDROID + name.split(":", 1)[1]
        # Raw is a diagnostic annotation, never the authoritative value. A
        # compiled integer must not be replaced by a different Raw string.
        value = re.sub(r'\s+\(Raw: "(?:\\.|[^"\\])*"\)\s*$', "", value).strip()
        quoted = re.match(r'^"((?:\\.|[^"\\])*)"', value)
        integer = re.fullmatch(r"\(type 0x(?:10|11|12)\)0x([0-9a-fA-F]+)", value.strip())
        if quoted:
            value = quoted[1].replace(r'\"', '"').replace('\\\\', '\\')
        elif integer:
            value = str(int(integer[1], 16))
        elif re.fullmatch(r"0x[0-9a-fA-F]+", value.strip()):
            value = str(int(value, 16))
        stack[-1][1].set(name, value)
    if root is None or root.tag != "manifest":
        raise ValueError("aapt2 did not return an Android manifest")
    return root


def verify_manifest(text, identity, production_id):
    """Reject identifiers which would collide with or route into production."""
    root = manifest_from_dump(text)
    package = identity["application_id"]
    if root.get("package") != package:
        raise ValueError("Merged manifest application ID mismatch")
    if root.get(ANDROID + "sharedUserId"):
        raise ValueError("Standalone test APK must not use sharedUserId")
    if root.findall("original-package") or root.findall("adopt-permissions"):
        raise ValueError("Standalone test APK must not adopt a production package or permissions")
    if root.get(ANDROID + "versionCodeMajor", "0") != "0":
        raise ValueError("Unexpected versionCodeMajor in standalone test APK")
    if len(root.findall("application")) != 1:
        raise ValueError("Expected exactly one manifest application")
    version = root.get(ANDROID + "versionCode", "")
    if not version.isdecimal() or int(version) < 1:
        raise ValueError(f"Missing or invalid versionCode: {version!r}")
    if "version_code" in identity and int(version) != identity["version_code"]:
        raise ValueError("APK versionCode does not match the test build plan")

    def owned(value):
        return value == package or value.startswith(package + ".")

    def production(value):
        return (value == production_id or value.startswith(production_id + ".")) and not owned(value)

    authorities = set()
    permissions = set()
    custom_schemes = set()
    file_provider = False
    messaging = False
    for node in root.iter():
        if node.tag == "provider":
            values = node.get(ANDROID + "authorities", "").split(";")
            for authority in values:
                if not authority or not owned(authority):
                    raise ValueError(f"Provider authority is not scoped to {package}: {authority}")
                if authority in authorities:
                    raise ValueError(f"Duplicate provider authority: {authority}")
                authorities.add(authority)
            if values == [package + ".FileProvider"]:
                # Component class names can be obfuscated; the runtime URI
                # contract is the package-derived authority, not a class name.
                file_provider = True
        if node.tag in {"permission", "permission-tree", "permission-group"}:
            permission = node.get(ANDROID + "name", "")
            if not owned(permission):
                raise ValueError(f"Custom permission is not scoped to the test package: {permission}")
            permissions.add(permission)
        if node.tag == "intent-filter" and node.get(ANDROID + "autoVerify", "0") not in {"0", "false"}:
            raise ValueError("Test APK declares verified App Links without a separate test association")
        for attr in ("process", "taskAffinity"):
            value = node.get(ANDROID + attr, "")
            if value and not owned(value) and not (attr == "process" and value.startswith(":")):
                raise ValueError(f"Manifest {attr} is shared with another application: {value}")
        for attr in ("permission", "readPermission", "writePermission"):
            if production(node.get(ANDROID + attr, "")):
                raise ValueError(f"Test component uses a production {attr}")
        if node.tag in {"uses-permission", "uses-permission-sdk-23", "category"}:
            if production(node.get(ANDROID + "name", "")):
                raise ValueError("Test APK requests a production permission or push category")
        if node.tag == "data":
            scheme = node.get(ANDROID + "scheme", "")
            if scheme and scheme not in WEB_SCHEMES:
                if not owned(scheme):
                    raise ValueError(f"Test APK claims a shared custom URI scheme: {scheme}")
                custom_schemes.add(scheme)
        if node.tag == "action" and node.get(ANDROID + "name") == "com.google.firebase.MESSAGING_EVENT":
            messaging = True
    if not file_provider:
        raise ValueError("ChromeFileProvider is missing from the merged manifest")
    return {"application_id": package, "version_code": int(version), "shared_uid": False,
            "provider_authorities": sorted(authorities), "custom_permissions": sorted(permissions),
            "custom_uri_schemes": sorted(custom_schemes), "verified_app_links": [],
            "declares_firebase_messaging_service": messaging}
