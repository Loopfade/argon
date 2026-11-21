"""Keep the playback integration test's immutable dependency pins fail-closed."""
import hashlib
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

import check_background_playback_flow as flow


class BackgroundDependencyFetchTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.cache_patch = patch.object(flow, "CACHE", Path(temporary.name))
        self.cache_patch.start()
        self.addCleanup(self.cache_patch.stop)

    def test_androidx_fallbacks_use_only_pinned_builds(self):
        original = flow.DEPENDENCIES["preference.aar"][0]
        aar_urls = flow.dependency_urls("preference.aar", original)
        self.assertEqual(aar_urls[0], original)
        self.assertEqual(len(aar_urls), 2)
        self.assertTrue(aar_urls[1].startswith(flow.PREFERENCE_CI_ROOT))
        self.assertEqual(
            len(flow.dependency_urls(
                "preference-sources.jar",
                flow.DEPENDENCIES["preference-sources.jar"][0])), 3)
        self.assertEqual(
            flow.dependency_urls("android-json.jar",
                                 flow.DEPENDENCIES["android-json.jar"][0]),
            [flow.DEPENDENCIES["android-json.jar"][0]])

    def test_404_falls_back_only_to_same_checksum(self):
        payload = b"archive with pinned checksum"
        expected = hashlib.sha256(payload).hexdigest()
        url = flow.DEPENDENCIES["preference-sources.jar"][0]
        with patch.dict(flow.DEPENDENCIES, {
                "preference-sources.jar": (url, expected)}):
            attempts = []

            def open_url(candidate, timeout):
                attempts.append(candidate)
                if len(attempts) < 3:
                    raise HTTPError(candidate, 404, "missing", {}, None)
                return io.BytesIO(payload)

            with patch.object(flow.urllib.request, "urlopen",
                              side_effect=open_url):
                path = flow.dependency("preference-sources.jar")
            self.assertEqual(attempts[-1], flow.PREVIOUS_PREFERENCE_SOURCES)
            self.assertEqual(path.read_bytes(), payload)

    def test_mismatched_checksum_never_falls_back_or_caches(self):
        name = "preference.aar"
        url = flow.DEPENDENCIES[name][0]
        with patch.dict(flow.DEPENDENCIES, {
                name: (url, hashlib.sha256(b"expected").hexdigest())}):
            with patch.object(flow.urllib.request, "urlopen",
                              return_value=io.BytesIO(b"wrong")) as download:
                with self.assertRaisesRegex(ValueError, "expected SHA-256"):
                    flow.dependency(name)
            download.assert_called_once()
            self.assertFalse((flow.CACHE / name).exists())

    def test_all_404s_report_exact_dependency_and_urls(self):
        name = "preference.aar"
        url = flow.DEPENDENCIES[name][0]

        def missing(candidate, timeout):
            raise HTTPError(candidate, 404, "missing", {}, None)

        with patch.object(flow.urllib.request, "urlopen",
                          side_effect=missing):
            with self.assertRaisesRegex(RuntimeError, name) as raised:
                flow.dependency(name)
        self.assertIn(url, str(raised.exception))
        self.assertIn(flow.PREFERENCE_CI_ROOT, str(raised.exception))

    def test_verified_cache_avoids_network(self):
        name = "preference.aar"
        payload = b"verified"
        path = flow.CACHE / name
        path.write_bytes(payload)
        with patch.dict(flow.DEPENDENCIES, {
                name: (flow.DEPENDENCIES[name][0],
                       hashlib.sha256(payload).hexdigest())}):
            with patch.object(flow.urllib.request, "urlopen") as download:
                self.assertEqual(flow.dependency(name), path)
            download.assert_not_called()


if __name__ == "__main__":
    unittest.main()
