"""Build the real patched Chromium constraint method for the BoringSSL test."""
from pathlib import Path
import ssl
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import apply_scoped_ca as patch

FIXTURES = ROOT / "tests/fixtures/certificates"


def verify_upstream(source):
    method = (FIXTURES / "check_extra_constraints.cc.in").read_text().rstrip()
    if source.count(method) != 1:
        raise ValueError("Review the certificate constraint fixture against pinned Chromium")


def generate(output):
    output.mkdir(parents=True, exist_ok=True)
    source = '#include "net/cert/time_conversions.h"\n' + (
        FIXTURES / "check_extra_constraints.cc.in").read_text()
    patched = patch.patch_verifier(source)
    # The fixture is included inside a small delegate adapter; includes belong
    # to scoped_ca_test.cc. Its method body is otherwise unchanged.
    method = patched[patched.index("  void CheckExtraConstraints("):]
    (output / "scoped_ca_verifier.inc").write_text(method)
    der = ssl.PEM_cert_to_DER_cert((FIXTURES / "multi-san.pem").read_text())
    (output / "scoped_ca_leaf.inc").write_text(
        "constexpr uint8_t kMultiSanLeafDer[] = {\n" +
        ",".join(str(byte) for byte in der) + "\n};\n")


if __name__ == "__main__":
    generate(Path(sys.argv[1]))
