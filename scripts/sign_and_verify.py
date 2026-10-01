#!/usr/bin/env python3
"""Sign and verify a single-ABI APK, then write checksums and build metadata."""
import argparse
import base64
import binascii
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import zipfile

from configure_build import TARGET_ABIS, target_cpu

ROOT = Path(__file__).resolve().parents[1]


def run(argv, **kwargs):
    return subprocess.run([str(x) for x in argv], check=True, **kwargs)


def signing_tools(sdk: Path, jdk: Path) -> Path:
    candidates = sorted(
        (sdk / "build-tools").glob("*/apksigner"),
        key=lambda path: tuple(int(n) for n in re.findall(r"\d+", path.parent.name)),
    )
    if not candidates:
        raise SystemExit("Android SDK apksigner not found")
    build_tools = candidates[-1].parent
    required = [build_tools / tool for tool in ("apksigner", "zipalign", "aapt2")]
    required += [jdk / "bin" / tool for tool in ("java", "keytool")]
    for tool in required:
        if not tool.is_file() or not os.access(tool, os.X_OK):
            raise SystemExit(f"Missing or non-executable signing tool: {tool}")
    return build_tools


def verify_apk_abi(apk: Path, expected_abi: str):
    with zipfile.ZipFile(apk) as archive:
        if archive.testzip() is not None:
            raise SystemExit("APK ZIP integrity failure")
        abis = {p.split('/')[1] for p in archive.namelist()
                if p.startswith('lib/') and p.endswith('.so')}
        if abis != {expected_abi}:
            raise SystemExit(
                f"Unexpected APK native ABIs: {sorted(abis)}; expected {expected_abi}"
            )


def write_release_key(key: Path, env):
    for name in ("TITANIUM_RU_KEYSTORE_BASE64", "TITANIUM_RU_STORE_PASSWORD",
                 "TITANIUM_RU_KEY_PASSWORD", "TITANIUM_RU_KEY_ALIAS"):
        if not env.get(name):
            raise SystemExit(f"Missing release signing secret: {name}")
    try:
        data = base64.b64decode(env["TITANIUM_RU_KEYSTORE_BASE64"], validate=True)
    except (binascii.Error, ValueError):
        raise SystemExit("Release keystore must be valid single-line Base64") from None
    # Restrict permissions when creating the file, before writing any key bytes.
    descriptor = os.open(key, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as output:
        output.write(data)


def check_release_key(jdk: Path, env):
    with tempfile.TemporaryDirectory(prefix="argon-key-check-") as tmp:
        key = Path(tmp) / "signing.p12"
        write_release_key(key, env)
        # JDK source-file mode needs no external libraries. Read passwords only
        # from the environment; never include them in arguments or diagnostics.
        result = run([jdk / "bin/java", ROOT / "scripts/VerifyReleaseKey.java", key],
                     env=env, capture_output=True, text=True)
        print(result.stdout.strip())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--apk", type=Path)
    action.add_argument("--check-tools", action="store_true")
    parser.add_argument("--sdk", required=True, type=Path)
    parser.add_argument("--jdk", required=True, type=Path)
    parser.add_argument("--mode", choices=["test", "release"])
    parser.add_argument("--arch", type=target_cpu, default="arm64")
    args = parser.parse_args()
    env = os.environ.copy()
    env["JAVA_HOME"] = str(args.jdk.resolve())
    # apksigner's launcher invokes `java` by name rather than consulting
    # JAVA_HOME. Prefer the JDK baked into the prepared image over any runner
    # Java installation.
    env["PATH"] = str(args.jdk.resolve() / "bin") + os.pathsep + env.get("PATH", "")
    if args.check_tools:
        build_tools = signing_tools(args.sdk, args.jdk)
        run([args.jdk / "bin/java", "-version"], env=env)
        run([args.jdk / "bin/keytool", "-help"], env=env, capture_output=True)
        run([build_tools / "apksigner", "version"], env=env)
        run([build_tools / "aapt2", "version"], env=env)
        with tempfile.TemporaryDirectory(prefix="argon-tools-") as tmp:
            source, aligned = Path(tmp) / "probe.zip", Path(tmp) / "aligned.zip"
            with zipfile.ZipFile(source, "w") as archive:
                archive.writestr("probe", b"Argon signing tools")
            run([build_tools / "zipalign", "-f", "-P", "16", "4", source, aligned])
            run([build_tools / "zipalign", "-c", "-P", "16", "4", aligned])
        print(f"Signing tools verified: {build_tools}")
        if args.mode == "release":
            try:
                check_release_key(args.jdk, env)
            except subprocess.CalledProcessError:
                raise SystemExit("Release key check failed before compilation: check PKCS12, "
                                 "passwords, alias and certificate validity") from None
        return
    if args.mode is None:
        parser.error("--mode is required when signing an APK")
    abi = TARGET_ABIS[args.arch]
    # Reject a wrong or mixed architecture before accessing signing keys.
    verify_apk_abi(args.apk, abi)
    lock = json.loads((ROOT / "build-lock.json").read_text())
    build_tools = signing_tools(args.sdk, args.jdk)
    artifacts = ROOT / "artifacts" / abi
    artifacts.mkdir(parents=True, exist_ok=True)
    name = f'Argon-{lock["chromium_version"]}-{args.mode}-{abi}.apk'
    output = artifacts / name
    (ROOT / ".build").mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="argon-sign-", dir=ROOT / ".build") as tmp:
        # Alignment must happen before apksigner; never modify a signed APK.
        aligned = Path(tmp) / "aligned.apk"
        run([build_tools / "zipalign", "-f", "-P", "16", "4", args.apk, aligned])
        key_type = "PKCS12" if args.mode == "release" else "JKS"
        key = Path(tmp) / ("signing.p12" if args.mode == "release" else "signing.jks")
        if args.mode == "release":
            write_release_key(key, env)
        else:
            # This temporary key is never published or committed to git.
            env["TITANIUM_RU_STORE_PASSWORD"] = "android"
            env["TITANIUM_RU_KEY_PASSWORD"] = "android"
            env["TITANIUM_RU_KEY_ALIAS"] = "argon-test"
            run([args.jdk / "bin/keytool", "-genkeypair", "-noprompt",
                 "-keystore", key, "-storetype", "JKS",
                 "-storepass:env", "TITANIUM_RU_STORE_PASSWORD",
                 "-keypass:env", "TITANIUM_RU_KEY_PASSWORD",
                 "-alias", env["TITANIUM_RU_KEY_ALIAS"], "-keyalg", "RSA",
                 "-keysize", "3072", "-validity", "3650", "-dname",
                 "CN=Argon Temporary Test Build"], env=env)
        run([build_tools / "apksigner", "sign", "--ks", key,
             "--ks-type", key_type,
             "--ks-pass", "env:TITANIUM_RU_STORE_PASSWORD",
             "--key-pass", "env:TITANIUM_RU_KEY_PASSWORD",
             "--ks-key-alias", env["TITANIUM_RU_KEY_ALIAS"],
             "--out", output, aligned], env=env)
        verification = run([build_tools / "apksigner", "verify", "--verbose",
                            "--print-certs", output], env=env, capture_output=True, text=True)
        (artifacts / "apk-signature.txt").write_text(verification.stdout)
        run([args.jdk / "bin/keytool", "-exportcert", "-rfc", "-keystore", key,
             "-storetype", key_type,
             "-storepass:env", "TITANIUM_RU_STORE_PASSWORD", "-alias",
             env["TITANIUM_RU_KEY_ALIAS"], "-file", artifacts / "signing-certificate.pem"], env=env)
    run([build_tools / "zipalign", "-c", "-P", "16", "4", output])
    badging = run([build_tools / "aapt2", "dump", "badging", output], capture_output=True, text=True).stdout
    if not re.search(r"^package: name='" + re.escape(lock["application_id"]) + "'", badging, re.M):
        raise SystemExit("APK application ID mismatch")
    if "application-label:'Argon'" not in badging:
        raise SystemExit("APK application label mismatch")
    verify_apk_abi(output, abi)
    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    (artifacts / (name + ".sha256")).write_text(f"{digest}  {name}\n")
    metadata = {
        "source_commit": subprocess.check_output(["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True).strip(),
        "inputs": lock,
        "ca_der_sha256": json.loads((ROOT / "certificates/ministry-ca-lock.json").read_text())["der_sha256"],
        "signing_mode": args.mode,
        "apk_sha256": digest,
        "abi": abi,
        "target_cpu": args.arch,
        "device_smoke_test": "not run",
    }
    (artifacts / "build-info.json").write_text(json.dumps(metadata, indent=2) + "\n")
    shutil.copy(ROOT / "LICENSE", artifacts / "Titanium-LICENSE.txt")
    shutil.copy(ROOT / "chromium/src/LICENSE", artifacts / "Chromium-LICENSE.txt")
    shutil.copy(ROOT / "licenses/Ruthenium-BSD-3-Clause.txt", artifacts / "Ruthenium-LICENSE.txt")
    print(f"Verified {name}: {digest}")


if __name__ == "__main__":
    main()
