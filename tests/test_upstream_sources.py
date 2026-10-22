"""Keep source pins strict while retrying transient upstream failures."""
import base64
import binascii
import io
import unittest
from unittest.mock import patch
import urllib.error

import upstream_sources as sources


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

    def test_invalid_source_encoding_is_not_silently_accepted(self):
        with patch.object(sources.urllib.request, "urlopen", return_value=io.BytesIO(b"invalid!")):
            with self.assertRaises(binascii.Error):
                sources.download(sources.chromium_url(self.revision, "DEPS"))


if __name__ == "__main__":
    unittest.main()
