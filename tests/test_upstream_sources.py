"""Keep source pins strict while retrying transient upstream failures."""
import base64
import binascii
import io
from contextlib import redirect_stdout
import unittest
from unittest.mock import patch
import urllib.error

import upstream_sources as sources
import check_upstream_patches as fixture_checker
from threading import Lock
from time import sleep as real_sleep


class UpstreamSourceTests(unittest.TestCase):
    revision = "a" * 40

    def test_only_immutable_revisions_and_relative_paths_are_accepted(self):
        for revision in ("main", "refs/tags/156.0.8078.47", "a" * 39):
            with self.subTest(revision=revision), self.assertRaises(ValueError):
                sources.chromium_url(revision, "chrome/VERSION")
        for path in ("/DEPS", "../DEPS", "chrome/../DEPS", ""):
            with self.subTest(path=path), self.assertRaises(ValueError):
                sources.chromium_url(self.revision, path)

    def test_gitiles_is_decoded_and_json_metadata_is_preserved(self):
        url = sources.chromium_url(self.revision, "chrome/VERSION")
        with patch.object(sources.urllib.request, "urlopen",
                          return_value=io.BytesIO(base64.b64encode(b"MAJOR=156\n"))) as request:
            self.assertEqual(sources.download(url), "MAJOR=156\n")
            request.assert_called_once_with(url, timeout=60)
        with patch.object(sources.urllib.request, "urlopen",
                          return_value=io.BytesIO(b'{"tree": []}')):
            self.assertEqual(sources.download("https://api.github.com/repos/owner/repo/git/trees/sha"),
                             '{"tree": []}')

    def test_rate_limits_and_service_errors_are_retried_with_the_same_pin(self):
        url = sources.chromium_url(self.revision, "DEPS")
        failures = [urllib.error.HTTPError(url, code, "temporary", {}, None)
                    for code in (429, 503)]
        with patch.object(sources.urllib.request, "urlopen", side_effect=[
                *failures, io.BytesIO(base64.b64encode(b"pinned DEPS"))]) as request, \
                patch.object(sources.time, "sleep") as delay:
            self.assertEqual(sources.download(url), "pinned DEPS")
            self.assertEqual(request.call_count, 3)
            self.assertTrue(all(call.args[0] == url for call in request.call_args_list))
            self.assertEqual([call.args[0] for call in delay.call_args_list], [30, 2])

    def test_missing_files_and_exhausted_retries_remain_failures(self):
        url = sources.chromium_url(self.revision, "missing")
        for code, attempts in ((404, 1), (503, 3)):
            with self.subTest(code=code), \
                    patch.object(sources.urllib.request, "urlopen", side_effect=
                                 urllib.error.HTTPError(url, code, "failure", {}, None)) as request, \
                    patch.object(sources.time, "sleep"), \
                    self.assertRaises(urllib.error.HTTPError):
                sources.download(url)
            self.assertEqual(request.call_count, attempts)

    def test_server_retry_after_is_respected(self):
        url = sources.chromium_url(self.revision, "DEPS")
        with patch.object(sources.urllib.request, "urlopen", side_effect=[
                urllib.error.HTTPError(url, 429, "rate limit", {"Retry-After": "7"}, None),
                io.BytesIO(base64.b64encode(b"DEPS"))]), \
                patch.object(sources.time, "sleep") as delay:
            self.assertEqual(sources.download(url), "DEPS")
            delay.assert_called_once_with(7)

    def test_github_credentials_are_only_sent_to_the_github_api(self):
        with patch.dict(sources.os.environ, {"GH_TOKEN": "test-token"}), \
                patch.object(sources.urllib.request, "urlopen",
                             return_value=io.BytesIO(b'{"tree": []}')) as request:
            sources.download("https://api.github.com/repos/owner/repo/git/trees/sha")
            self.assertEqual(request.call_args.args[0].get_header("Authorization"),
                             "Bearer test-token")
        url = sources.chromium_url(self.revision, "DEPS")
        with patch.dict(sources.os.environ, {"GH_TOKEN": "test-token"}), \
                patch.object(sources.urllib.request, "urlopen",
                             return_value=io.BytesIO(base64.b64encode(b"DEPS"))) as request:
            sources.download(url)
            request.assert_called_once_with(url, timeout=60)

    def test_official_mirror_preserves_commit_path_and_plain_source_bytes(self):
        url = sources.chromium_url(self.revision, "chrome/VERSION")
        mirror = "https://raw.githubusercontent.com/chromium/chromium/" + self.revision + "/chrome/VERSION"
        with patch.dict(sources.os.environ, {"GH_TOKEN": "test-token"}), \
                patch.object(sources.urllib.request, "urlopen",
                             return_value=io.BytesIO(b"MAJOR=156\n")) as request:
            self.assertEqual(sources.download(url, prefer_mirror=True), "MAJOR=156\n")
            request.assert_called_once_with(mirror, timeout=60)
        for path in ("main/DEPS?format=TEXT", self.revision + "/../DEPS?format=TEXT",
                     self.revision + "/%2e%2e/DEPS?format=TEXT"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                sources.chromium_mirror_url(sources.CHROMIUM_SOURCE + path)

    def test_unavailable_mirror_falls_back_to_the_same_canonical_pin(self):
        url = sources.chromium_url(self.revision, "DEPS")
        mirror = sources.chromium_mirror_url(url)
        for code, attempts in ((404, 1), (503, 3)):
            with self.subTest(code=code), patch.object(sources.time, "sleep"), \
                    redirect_stdout(io.StringIO()), \
                    patch.object(sources.urllib.request, "urlopen", side_effect=[
                        *[urllib.error.HTTPError(mirror, code, "unavailable", {}, None)
                          for _ in range(attempts)],
                        io.BytesIO(base64.b64encode(b"pinned DEPS"))]) as request:
                self.assertEqual(sources.download(url, prefer_mirror=True), "pinned DEPS")
                self.assertEqual([call.args[0] for call in request.call_args_list],
                                 [mirror] * attempts + [url])

    def test_mirror_does_not_hide_corrupt_source_or_reroute_github_metadata(self):
        url = sources.chromium_url(self.revision, "DEPS")
        with patch.object(sources.urllib.request, "urlopen", return_value=io.BytesIO(b"\xff")) as request:
            with self.assertRaises(UnicodeDecodeError):
                sources.download(url, prefer_mirror=True)
            self.assertEqual(request.call_count, 1)
        metadata = "https://api.github.com/repos/owner/repo/git/trees/sha"
        with patch.object(sources.urllib.request, "urlopen", return_value=io.BytesIO(b'{"tree": []}')) as request:
            self.assertEqual(sources.download(metadata, prefer_mirror=True), '{"tree": []}')
            self.assertEqual(request.call_count, 1)

    def test_bulk_fixture_keeps_source_paths_when_requests_finish_out_of_order(self):
        urls = {name: sources.chromium_url(self.revision, name)
                for name in ("slow", "a", "b", "c", "d", "e")}
        active = maximum = 0
        lock = Lock()

        def read(url, *, prefer_mirror):
            nonlocal active, maximum
            self.assertTrue(prefer_mirror)
            with lock:
                active += 1
                maximum = max(maximum, active)
            real_sleep(0.03 if "/slow?" in url else 0.005)
            with lock:
                active -= 1
            return url

        with patch.object(fixture_checker, "download", side_effect=read), \
                patch.object(fixture_checker.time, "sleep"), \
                redirect_stdout(io.StringIO()) as progress:
            self.assertEqual(fixture_checker.fetch_sources(urls), urls)
        self.assertIn("Исходники [6/6]", progress.getvalue())
        self.assertGreater(maximum, 1)
        self.assertLessEqual(maximum, 4)

    def test_bulk_fixture_never_accepts_a_failed_source(self):
        with patch.object(fixture_checker, "download", side_effect=ValueError("bad source")), \
                patch.object(fixture_checker.time, "sleep"), \
                self.assertRaisesRegex(ValueError, "bad source"):
            fixture_checker.fetch_sources({"DEPS": sources.chromium_url(self.revision, "DEPS")})

    def test_invalid_source_encoding_is_not_silently_accepted(self):
        with patch.object(sources.urllib.request, "urlopen", return_value=io.BytesIO(b"invalid!")):
            with self.assertRaises(binascii.Error):
                sources.download(sources.chromium_url(self.revision, "DEPS"))


if __name__ == "__main__":
    unittest.main()
