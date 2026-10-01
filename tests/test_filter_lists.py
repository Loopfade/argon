import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import fetch_pinned_filter_lists


class FakeResponse:
    status = 200

    def __init__(self, data):
        self.data = data

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return self.data


class FilterListTests(unittest.TestCase):
    def entries(self):
        payloads = {"https://example.test/b": b"second", "https://example.test/a": b"first"}
        entries = [
            {"name": url.rsplit("/", 1)[-1], "url": url, "sha256": hashlib.sha256(data).hexdigest()}
            for url, data in payloads.items()
        ]
        return entries, payloads

    def test_lock_has_https_urls_and_sha256_digests(self):
        entries = json.loads((ROOT / "build-lock.json").read_text())["filter_lists"]
        self.assertEqual(len(entries), 3)
        self.assertEqual(len({entry["name"] for entry in entries}), len(entries))
        for entry in entries:
            self.assertTrue(entry["url"].startswith("https://"))
            self.assertRegex(entry["sha256"], r"^[0-9a-f]{64}$")

    def test_volatile_adblock_metadata_is_removed_before_hashing(self):
        stable = b"[Adblock Plus 2.0]\n||example.test^\n"
        downloaded = (
            b"[Adblock Plus 2.0]\n"
            b"! Checksum: changes\n"
            b"! Version: changes\n"
            b"! Last modified: changes\n"
            b"||example.test^\n"
        )
        entry = {
            "name": "filters.txt",
            "url": "https://example.test/filters.txt",
            "sha256": hashlib.sha256(stable).hexdigest(),
            "strip_volatile_headers": True,
        }

        def opener(_request, **_kwargs):
            return FakeResponse(downloaded)

        self.assertEqual(fetch_pinned_filter_lists.download(entry, opener), stable)

    def test_fetch_verifies_and_concatenates_in_url_order(self):
        entries, payloads = self.entries()

        def opener(request, **_kwargs):
            return FakeResponse(payloads[request.full_url])

        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "filters.txt"
            fetch_pinned_filter_lists.fetch(output, entries, opener)
            self.assertEqual(output.read_bytes(), b"firstsecond")

    def test_digest_mismatch_does_not_replace_existing_output(self):
        entries, payloads = self.entries()
        entries[0]["sha256"] = "0" * 64

        def opener(request, **_kwargs):
            return FakeResponse(payloads[request.full_url])

        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "filters.txt"
            output.write_bytes(b"known-good")
            with self.assertRaisesRegex(ValueError, "digest mismatch"):
                fetch_pinned_filter_lists.fetch(output, entries, opener)
            self.assertEqual(output.read_bytes(), b"known-good")


if __name__ == "__main__":
    unittest.main()
