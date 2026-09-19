#!/usr/bin/env python3
"""Sign and verify a single-ABI APK, then write checksums and build metadata."""
import argparse
import base64
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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apk", required=True, type=Path)
    parser.add_argument("--sdk", required=True, type=Path)
    parser.add_argument("--jdk", required=True, type=Path)
    parser.add_argument("--mode", choices=["test", "release"], required=True)
    parser.add_argument("--arch", type=target_cpu, default="arm64")
    args = parser.parse_args()
    abi = TARGET_ABIS[args.arch]
    # Reject a wrong or mixed architecture before accessing signing keys.
    verify_apk_abi(args.apk, abi)
    lock = json.loads((ROOT / "build-lock.json").read_text())
    tools = sorted((args.sdk / "build-tools").glob("*/apksigner"))
    if not tools:
        raise SystemExit("Android SDK apksigner not found")
    build_tools = tools[-1].parent
    artifacts = ROOT / "artifacts" / abi
    artifacts.mkdir(parents=True, exist_ok=True)
    name = f'Titanium-RU-{lock["chromium_version"]}-{args.mode}-{abi}.apk'
    output = artifacts / name
    env = os.environ.copy()
    env["JAVA_HOME"] = str(args.jdk)
    with tempfile.TemporaryDirectory(prefix="titanium-ru-sign-", dir=ROOT / ".build") as tmp:
        key = Path(tmp) / "signing.jks"
        if args.mode == "release":
            for var in ["TITANIUM_RU_KEYSTORE_BASE64", "TITANIUM_RU_STORE_PASSWORD",
                        "TITANIUM_RU_KEY_PASSWORD", "TITANIUM_RU_KEY_ALIAS"]:
                if not env.get(var):
                    raise SystemExit(f"Missing release signing secret: {var}")
            key.write_bytes(base64.b64decode(env["TITANIUM_RU_KEYSTORE_BASE64"], validate=True))
            key.chmod(0o600)
        else:
            # This temporary key is never published or committed to git.
            env["TITANIUM_RU_STORE_PASSWORD"] = "android"
            env["TITANIUM_RU_KEY_PASSWORD"] = "android"
            env["TITANIUM_RU_KEY_ALIAS"] = "titanium-ru-test"
            run([args.jdk / "bin/keytool", "-genkeypair", "-noprompt",
                 "-keystore", key, "-storetype", "JKS",
                 "-storepass:env", "TITANIUM_RU_STORE_PASSWORD",
                 "-keypass:env", "TITANIUM_RU_KEY_PASSWORD",
                 "-alias", env["TITANIUM_RU_KEY_ALIAS"], "-keyalg", "RSA",
                 "-keysize", "3072", "-validity", "3650", "-dname",
                 "CN=Titanium RU Temporary Test Build"], env=env)
        run([build_tools / "apksigner", "sign", "--ks", key,
             "--ks-pass", "env:TITANIUM_RU_STORE_PASSWORD",
             "--key-pass", "env:TITANIUM_RU_KEY_PASSWORD",
             "--ks-key-alias", env["TITANIUM_RU_KEY_ALIAS"],
             "--out", output, args.apk], env=env)
        verification = run([build_tools / "apksigner", "verify", "--verbose",
                            "--print-certs", output], env=env, capture_output=True, text=True)
        (artifacts / "apk-signature.txt").write_text(verification.stdout)
        run([args.jdk / "bin/keytool", "-exportcert", "-rfc", "-keystore", key,
             "-storepass:env", "TITANIUM_RU_STORE_PASSWORD", "-alias",
             env["TITANIUM_RU_KEY_ALIAS"], "-file", artifacts / "signing-certificate.pem"], env=env)
    run([build_tools / "zipalign", "-c", "-P", "16", "4", output])
    badging = run([build_tools / "aapt2", "dump", "badging", output], capture_output=True, text=True).stdout
    if not re.search(r"^package: name='" + re.escape(lock["application_id"]) + "'", badging, re.M):
        raise SystemExit("APK application ID mismatch")
    if "application-label:'Titanium RU'" not in badging:
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
