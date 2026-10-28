#!/usr/bin/env python3
"""Plan the manual pipeline and verify reusable images and signed APKs."""
import argparse
import base64
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from urllib.parse import urlencode
import zipfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from configure_build import TARGET_ABIS
from build_identity import application_identity
from sign_and_verify import check_release_key, signing_tools, verify_apk_abi, write_release_key

IMAGE_LABEL = "io.argon.prepared-inputs"
WORKFLOW = ".github/workflows/build-all.yml"
WORKFLOWS = (WORKFLOW, ".github/workflows/build-all-no-publish.yml")


def command(argv, **kwargs):
    return subprocess.check_output([str(arg) for arg in argv], **kwargs)


def api(path, paginate=False):
    args = ["gh", "api", f"repos/{os.environ['GITHUB_REPOSITORY']}/{path}"]
    if paginate:
        args += ["--paginate", "--slurp"]
    return json.loads(command(args, text=True))


def emit(**values):
    with open(os.environ["GITHUB_OUTPUT"], "a") as stream:
        for name, value in values.items():
            if isinstance(value, bool):
                value = str(value).lower()
            if "\n" in str(value) or "\r" in str(value):
                raise ValueError("Multiline workflow output")
            stream.write(f"{name}={value}\n")


def digest_inputs(files):
    digest = hashlib.sha256()
    for name, data in sorted(files.items()):
        digest.update(name.encode() + b"\0" + str(len(data)).encode() + b"\0" + data)
    return digest.hexdigest()


def image_fingerprint(root=ROOT):
    spec = importlib.util.spec_from_file_location("prepared_inputs", root / ".github/ci/check-prepared-inputs.py")
    prepared = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(prepared)
    files = prepared.inputs(root)
    for name in ("docker/chromium/Dockerfile", ".dockerignore", "args.gn",
                 "scripts/configure_build.py", "scripts/preflight.py",
                 "tests/CMakeLists.txt", "tests/scoped_ca_test.cc",
                 ".github/ci/check-prepared-inputs.py"):
        files[name] = (root / name).read_bytes()
    # The submodule's immutable revision is also validated by preflight.
    lock = json.loads((root / "build-lock.json").read_text())
    files["vanadium gitlink"] = lock["vanadium_commit"].encode()
    files["fingerprint schema"] = b"prepared-v1"
    return digest_inputs(files)


def artifact_fingerprint(sha, arch, mode, certificate, prepared, standalone_test=False):
    build = {
        "schema": 1, "source_commit": sha, "arch": arch, "mode": mode,
        "certificate": certificate, "prepared_inputs": prepared,
    }
    if standalone_test:
        build["application_variant"] = "standalone-test"
    return digest_inputs({"build": json.dumps(build, sort_keys=True).encode()})


def certificate_digest(path):
    pem = path.read_text()
    match = re.fullmatch(r"\s*-----BEGIN CERTIFICATE-----\s*([A-Za-z0-9+/=\s]+)"
                         r"-----END CERTIFICATE-----\s*", pem)
    if not match:
        raise ValueError("Invalid signing certificate PEM")
    return hashlib.sha256(base64.b64decode("".join(match[1].split()), validate=True)).hexdigest()


def release_certificate(jdk, env):
    check_release_key(jdk, env)
    with tempfile.TemporaryDirectory(prefix="argon-certificate-") as tmp:
        key = Path(tmp) / "signing.p12"
        write_release_key(key, env)
        try:
            der = command([jdk / "bin/keytool", "-exportcert", "-keystore", key,
                           "-storetype", "PKCS12", "-storepass:env", "TITANIUM_RU_STORE_PASSWORD",
                           "-alias", env["TITANIUM_RU_KEY_ALIAS"]], env=env, stderr=subprocess.PIPE)
        except subprocess.CalledProcessError:
            raise ValueError("Cannot read the verified release certificate") from None
    return hashlib.sha256(der).hexdigest()


def verify_artifact(root, sha, arch, mode, fingerprint, certificate, sdk, jdk):
    abi = TARGET_ABIS[arch]
    candidates = list(root.rglob("build-info.json"))
    if len(candidates) != 1:
        raise ValueError("Expected exactly one artifact metadata file")
    metadata_path = candidates[0]
    metadata = json.loads(metadata_path.read_text())
    lock = json.loads((ROOT / "build-lock.json").read_text())
    identity = application_identity(lock, mode)
    standard_identity = application_identity(lock, mode, standalone_test=False)
    if (metadata.get("source_commit") != sha or metadata.get("target_cpu") != arch
            or metadata.get("abi") != abi or metadata.get("signing_mode") != mode
            or metadata.get("inputs") != lock
            or metadata.get("ca_der_sha256") != json.loads(
                (ROOT / "certificates/ministry-ca-lock.json").read_text())["der_sha256"]
            or metadata.get("pipeline", {}).get("fingerprint") != fingerprint
            or metadata.get("application_identity", standard_identity) != identity):
        raise ValueError("APK provenance does not match this build")
    directory = metadata_path.parent
    name = f"Argon-{lock['chromium_version']}-{mode}-{abi}.apk"
    apk = directory / name
    if list(root.rglob("*.apk")) != [apk]:
        raise ValueError("Unexpected APK payload")
    digest = hashlib.sha256(apk.read_bytes()).hexdigest()
    if (metadata.get("apk_sha256") != digest
            or (directory / f"{name}.sha256").read_text().split() != [digest, name]):
        raise ValueError("APK checksum mismatch")
    signer = certificate_digest(directory / "signing-certificate.pem")
    if mode == "release" and signer != certificate:
        raise ValueError("APK uses a different release certificate")
    tools = signing_tools(sdk, jdk)
    env = {**os.environ, "JAVA_HOME": str(jdk),
           "PATH": str(jdk / "bin") + os.pathsep + os.environ.get("PATH", "")}
    verification = command([tools / "apksigner", "verify", "--verbose", "--print-certs", apk],
                           text=True, env=env)
    signers = re.findall(r"^Signer #\d+ certificate SHA-256 digest: ([0-9a-fA-F:]+)$", verification, re.M)
    if [value.replace(":", "").lower() for value in signers] != [signer]:
        raise ValueError("APK signature does not match its certificate")
    subprocess.run([str(tools / "zipalign"), "-c", "-P", "16", "4", str(apk)], check=True)
    badging = command([tools / "aapt2", "dump", "badging", apk], text=True)
    if (not re.search(r"^package: name='" + re.escape(identity["application_id"]) + "'", badging, re.M)
            or f"application-label:'{identity['application_label']}'" not in badging):
        raise ValueError("APK package or application label mismatch")
    verify_apk_abi(apk, abi)
    for license_name in ("Chromium-LICENSE.txt", "Ruthenium-LICENSE.txt", "Titanium-LICENSE.txt"):
        if not (directory / license_name).is_file():
            raise ValueError("Missing source license")
    (directory / "apk-signature.txt").write_text(verification)
    return directory


def extract_artifact(artifact_id, destination):
    archive = destination / "download.zip"
    with archive.open("wb") as output:
        subprocess.run(["gh", "api", f"repos/{os.environ['GITHUB_REPOSITORY']}/actions/artifacts/{artifact_id}/zip"],
                       stdout=output, check=True)
    payload = destination / "payload"
    payload.mkdir()
    with zipfile.ZipFile(archive) as zipped:
        for entry in zipped.infolist():
            path = Path(entry.filename)
            if (path.is_absolute() or ".." in path.parts or "\\" in entry.filename
                    or (entry.external_attr >> 16) & 0o170000 == 0o120000):
                raise ValueError("Unsafe artifact archive path")
        zipped.extractall(payload)
    return payload


def reusable_artifact(name, sha, arch, mode, fingerprint, certificate, sdk, jdk):
    pages = api("actions/artifacts?" + urlencode({"per_page": 100, "name": name}), paginate=True)
    artifacts = [item for page in pages for item in page["artifacts"]
                 if item["name"] == name and not item["expired"]]
    for item in sorted(artifacts, key=lambda item: item["created_at"], reverse=True):
        run_id = item.get("workflow_run", {}).get("id")
        if not run_id:
            continue
        run = api(f"actions/runs/{run_id}")
        if (run.get("path") not in WORKFLOWS or run.get("event") != "workflow_dispatch"
                or run.get("head_sha") != sha or run.get("head_branch") != "main"
                or run.get("repository", {}).get("full_name") != os.environ["GITHUB_REPOSITORY"]
                or run.get("head_repository", {}).get("full_name") != os.environ["GITHUB_REPOSITORY"]):
            continue
        with tempfile.TemporaryDirectory(prefix="argon-reuse-") as tmp:
            try:
                payload = extract_artifact(item["id"], Path(tmp))
                directory = verify_artifact(payload, sha, arch, mode, fingerprint, certificate, sdk, jdk)
                target = ROOT / "artifacts" / TARGET_ABIS[arch]
                shutil.copytree(directory, target, dirs_exist_ok=True)
            except (ValueError, OSError, zipfile.BadZipFile, subprocess.CalledProcessError, SystemExit):
                print(f"::warning::Артефакт {item['id']} не прошёл проверку; поиск другого результата или повторная сборка.")
                continue
        return run_id
    return None


def plan(mode):
    if os.environ.get("GITHUB_REF") != "refs/heads/main":
        raise ValueError("Run the full pipeline from main; other refs must not replace its prepared image")
    sha = os.environ["GITHUB_SHA"]
    identity = application_identity(json.loads((ROOT / "build-lock.json").read_text()), mode)
    standalone_test = identity["variant"] == "standalone-test"
    jdk = Path(os.environ["JAVA_HOME"])
    sdk = Path(os.environ.get("ANDROID_HOME") or os.environ["ANDROID_SDK_ROOT"])
    certificate = release_certificate(jdk, os.environ) if mode == "release" else "temporary-test-key"
    prepared = image_fingerprint()
    builds, decisions = [], []
    outputs = {"prepared_inputs": prepared, "certificate": certificate}
    for arch in ("arm64", "arm"):
        fingerprint = artifact_fingerprint(sha, arch, mode, certificate, prepared, standalone_test)
        name = f"Argon-{arch}-{sha}-{fingerprint}"
        reused = reusable_artifact(name, sha, arch, mode, fingerprint, certificate, sdk, jdk)
        outputs[f"reuse_{arch}"] = bool(reused)
        outputs[f"fingerprint_{arch}"] = fingerprint
        if not reused:
            builds.append({"arch": arch, "fingerprint": fingerprint})
        decisions.append(f"- {arch}: " + (f"проверенный APK из запуска №{reused}" if reused else "сборка; подходящий проверенный APK не найден"))
    emit(**outputs, build=bool(builds), matrix=json.dumps({"include": builds}, separators=(",", ":")))
    with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as summary:
        signing_label = {"release": "релизная", "test": "тестовая"}[mode]
        summary.write("## План ручной сборки\n\n" + f"- Коммит: `{sha}`\n- Подпись: {signing_label}\n"
                      + f"- Приложение: `{identity['application_label']}` (`{identity['application_id']}`)\n"
                      + f"- Подготовленные входные данные: `{prepared}`\n" + "\n".join(decisions)
                      + "\n- Подготовленный образ: " + ("подобрать или собрать один раз" if builds else "не требуется; оба APK готовы") + "\n")


def probe_image(image):
    sys.path.insert(0, str(ROOT / ".github/ci"))
    resolver_spec = importlib.util.spec_from_file_location("image_resolver", ROOT / ".github/ci/resolve-prepared-image.py")
    resolver = importlib.util.module_from_spec(resolver_spec)
    resolver_spec.loader.exec_module(resolver)
    fingerprint = image_fingerprint()
    try:
        resolved = resolver.resolve(image, capture_errors=True)
    except subprocess.CalledProcessError as error:
        message = str(error.stderr or "")
        if not any(marker in message.lower() for marker in ("not found", "manifest unknown", "name unknown")):
            raise
        emit(exists=False, fingerprint=fingerprint, image="")
        return
    config = json.loads(command(["docker", "buildx", "imagetools", "inspect", resolved,
                                 "--format", "{{json .Image}}"], text=True))
    matches = config.get("config", {}).get("Labels", {}).get(IMAGE_LABEL) == fingerprint
    emit(exists=matches, fingerprint=fingerprint, image=resolved if matches else "")


def record(arch, mode, fingerprint, certificate, image):
    directory = ROOT / "artifacts" / TARGET_ABIS[arch]
    path = directory / "build-info.json"
    data = json.loads(path.read_text())
    identity = application_identity(json.loads((ROOT / "build-lock.json").read_text()), mode)
    standard_identity = application_identity(data["inputs"], mode, standalone_test=False)
    if data.get("application_identity", standard_identity) != identity:
        raise ValueError("Built APK uses a different application identity")
    signer = certificate_digest(directory / "signing-certificate.pem")
    if mode == "release" and signer != certificate:
        raise ValueError("Built APK uses a different release certificate")
    expected = artifact_fingerprint(os.environ["GITHUB_SHA"], arch, mode, certificate,
                                   image_fingerprint(), identity["variant"] == "standalone-test")
    if expected != fingerprint or data["source_commit"] != os.environ["GITHUB_SHA"]:
        raise ValueError("Build fingerprint changed after planning")
    data["pipeline"] = {"fingerprint": fingerprint, "prepared_inputs": image_fingerprint(),
                        "image": image, "certificate_sha256": signer}
    path.write_text(json.dumps(data, indent=2) + "\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("plan", "probe-image", "record", "fingerprint"))
    parser.add_argument("--mode", choices=("release", "test"), default="release")
    parser.add_argument("--arch", choices=("arm64", "arm"))
    parser.add_argument("--image")
    parser.add_argument("--fingerprint")
    parser.add_argument("--certificate")
    args = parser.parse_args()
    if args.action == "plan":
        plan(args.mode)
    elif args.action == "probe-image":
        probe_image(args.image)
    elif args.action == "fingerprint":
        print(image_fingerprint())
    else:
        record(args.arch, args.mode, args.fingerprint, args.certificate, args.image)


if __name__ == "__main__":
    main()
