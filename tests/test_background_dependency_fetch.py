"""Keep the playback integration test's immutable dependency pins fail-closed."""
import hashlib
import http.client
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

    def test_fallbacks_keep_pinned_builds_and_coordinates(self):
        original = flow.DEPENDENCIES["preference.aar"][0]
        aar_urls = flow.dependency_urls("preference.aar", original)
        self.assertEqual(aar_urls[0], original)
        self.assertEqual(len(aar_urls), 2)
        self.assertTrue(aar_urls[1].startswith(flow.PREFERENCE_CI_ROOT))
        self.assertEqual(
            len(flow.dependency_urls(
                "preference-sources.jar",
                flow.DEPENDENCIES["preference-sources.jar"][0])), 3)
        primary = flow.DEPENDENCIES["android-json.jar"][0]
        google = primary.replace("https://repo.maven.apache.org/maven2/",
                                 "https://maven-central.storage-download.googleapis.com/maven2/", 1)
        self.assertEqual(flow.dependency_urls("android-json.jar", primary),
                         [google, primary, primary.replace("repo.maven.apache.org", "repo1.maven.org", 1)])

    def test_google_mirror_avoids_requests_to_rate_limited_direct_hosts(self):
        name = "android-json.jar"
        url = flow.DEPENDENCIES[name][0]
        google = url.replace("https://repo.maven.apache.org/maven2/",
                             "https://maven-central.storage-download.googleapis.com/maven2/", 1)
        payload = b"the pinned Maven artifact from Google's mirror"

        def open_url(candidate, timeout):
            if candidate != google:
                raise HTTPError(candidate, 429, "rate limited", {}, None)
            return io.BytesIO(payload)

        with patch.dict(flow.DEPENDENCIES, {
                name: (url, hashlib.sha256(payload).hexdigest())}), \
                patch.object(flow.urllib.request, "urlopen", side_effect=open_url) as download:
            self.assertEqual(flow.dependency(name).read_bytes(), payload)
        download.assert_called_once_with(google, timeout=120)

    def test_google_mirror_wrong_checksum_never_falls_back_or_caches(self):
        name = "android-json.jar"
        with patch.object(flow.urllib.request, "urlopen",
                          return_value=io.BytesIO(b"wrong")) as download:
            with self.assertRaisesRegex(ValueError, "expected SHA-256"):
                flow.dependency(name)
        download.assert_called_once()
        self.assertFalse((flow.CACHE / name).exists())

    def test_dependency_429_respects_retry_after(self):
        name = "preference.aar"
        url = flow.DEPENDENCIES[name][0]
        payload = b"pinned archive"
        with patch.dict(flow.DEPENDENCIES, {
                name: (url, hashlib.sha256(payload).hexdigest())}), \
                patch("time.sleep") as delay, \
                patch.object(flow.urllib.request, "urlopen", side_effect=[
                    HTTPError(url, 429, "rate limited", {"Retry-After": "7"}, None),
                    io.BytesIO(payload)]) as download:
            self.assertEqual(flow.dependency(name).read_bytes(), payload)
        self.assertEqual([call.args[0] for call in download.call_args_list], [url, url])
        delay.assert_called_once_with(7)

    def test_persistent_rate_limit_uses_bounded_backoff_and_remains_a_failure(self):
        name = "preference.aar"

        def limited(candidate, timeout):
            raise HTTPError(candidate, 429, "rate limited", {}, None)

        with patch("time.sleep") as delay, \
                patch.object(flow.urllib.request, "urlopen", side_effect=limited) as download, \
                self.assertRaisesRegex(RuntimeError, "HTTP 429"):
            flow.dependency(name)
        self.assertEqual(download.call_count, 6)
        self.assertEqual([call.args[0] for call in delay.call_args_list], [30, 30, 30, 30])
        self.assertFalse((flow.CACHE / name).exists())

    def test_maven_404_falls_back_without_changing_version_or_checksum(self):
        name = "android-json.jar"
        url = flow.DEPENDENCIES[name][0]
        payload = b"the same pinned Android JSON archive"
        with patch.dict(flow.DEPENDENCIES, {
                name: (url, hashlib.sha256(payload).hexdigest())}), \
                patch.object(flow.urllib.request, "urlopen", side_effect=[
                    HTTPError(url, 404, "missing", {}, None), io.BytesIO(payload)]) as download:
            self.assertEqual(flow.dependency(name).read_bytes(), payload)
        self.assertEqual([call.args[0] for call in download.call_args_list],
                         [url.replace("https://repo.maven.apache.org/maven2/",
                                      "https://maven-central.storage-download.googleapis.com/maven2/", 1),
                          url])

    def test_maven_fallback_rejects_unreviewed_primary_endpoint(self):
        with self.assertRaises(ValueError):
            flow.dependency_urls("android-json.jar", "https://example.invalid/android-json.jar")

    def test_maven_fallback_rejects_wrong_checksum_without_caching(self):
        name = "android-json.jar"
        url = flow.DEPENDENCIES[name][0]
        with patch.dict(flow.DEPENDENCIES, {
                name: (url, hashlib.sha256(b"expected").hexdigest())}), \
                patch.object(flow.urllib.request, "urlopen", side_effect=[
                    HTTPError(url, 404, "missing", {}, None), io.BytesIO(b"wrong")]) as download:
            with self.assertRaisesRegex(ValueError, "expected SHA-256"):
                flow.dependency(name)
        self.assertEqual(download.call_count, 2)
        self.assertFalse((flow.CACHE / name).exists())

    def test_incomplete_dependency_body_is_retried_and_never_cached(self):
        class Socket:
            def makefile(self, *args, **kwargs):
                return io.BytesIO(b"HTTP/1.1 200 OK\r\nContent-Length: 100\r\n\r\npartial")

        # Use the real HTTP body reader, with a premature EOF after valid headers.
        partial = http.client.HTTPResponse(Socket())
        partial.begin()
        name = "preference.aar"
        url = flow.DEPENDENCIES[name][0]
        payload = b"complete archive"
        attempts = []

        def open_url(candidate, timeout):
            attempts.append(candidate)
            self.assertFalse((flow.CACHE / name).exists())
            return partial if len(attempts) == 1 else io.BytesIO(payload)

        with patch.dict(flow.DEPENDENCIES, {
                name: (url, hashlib.sha256(payload).hexdigest())}), \
                patch("time.sleep") as delay, \
                patch.object(flow.urllib.request, "urlopen", side_effect=open_url):
            self.assertEqual(flow.dependency(name).read_bytes(), payload)
        self.assertEqual(attempts, [url, url])
        self.assertTrue(partial.closed)
        delay.assert_called_once_with(1)

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
