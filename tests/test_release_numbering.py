"""Version-local release numbers, retries and occupied tag protection."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from release_policy import select_release_revision

VERSION = "155.0.8059.79"
TARGET = "a" * 40
OTHER = "b" * 40


def release(revision, target=OTHER, version=VERSION, **extra):
    return {"tag_name": f"v{version}-{revision}", "target_commitish": target,
            "draft": False, **extra}


def tag(revision, target=OTHER, version=VERSION):
    return {"name": f"v{version}-{revision}", "commit": {"sha": target}}


class ReleaseNumberingTests(unittest.TestCase):
    def select(self, releases=(), tags=()):
        return select_release_revision(VERSION, TARGET, list(releases), list(tags))

    def test_new_chromium_version_starts_at_one(self):
        self.assertEqual(self.select(), 1)
        self.assertEqual(self.select(
            [release(3, version="154.0.8037.126")],
            [tag(3, version="154.0.8037.126"),
             {"name": f"v{VERSION}", "commit": {"sha": OTHER}}],
        ), 1)

    def test_numeric_maximum_is_incremented_without_filling_gaps(self):
        self.assertEqual(self.select([release(2), release(10)], [tag(7)]), 11)
        self.assertEqual(self.select([release(3)], [tag(9)]), 10)

    def test_retry_reuses_same_source_even_after_a_newer_release(self):
        self.assertEqual(self.select(
            [release(2, TARGET), release(3)], [tag(2, TARGET), tag(3)],
        ), 2)

    def test_latest_revision_for_same_source_is_reused(self):
        self.assertEqual(self.select(
            [release(2, TARGET), release(4, TARGET)],
        ), 4)

    def test_interrupted_tag_only_publication_reuses_its_number(self):
        self.assertEqual(self.select(tags=[tag(2, TARGET)]), 2)
        self.assertEqual(self.select(tags=[tag(2)]), 3)

    def test_drafts_and_releases_without_tags_reserve_their_numbers(self):
        self.assertEqual(self.select([release(4, draft=True)]), 5)
        self.assertEqual(self.select([release(4, TARGET, draft=True)]), 4)
        self.assertEqual(self.select([release(6)]), 7)

    def test_two_serial_publications_select_distinct_numbers(self):
        first = self.select()
        second = select_release_revision(
            VERSION, OTHER, [release(first, TARGET)], [tag(first, TARGET)],
        )
        self.assertEqual((first, second), (1, 2))

    def test_unrelated_and_noncanonical_tags_do_not_change_the_counter(self):
        entries = [{"name": name, "commit": {"sha": OTHER}} for name in (
            "release-99", f"v{VERSION}-preview.99", f"v{VERSION}-0",
            f"v{VERSION}-01", "v155.0.8059.790-99",
        )]
        self.assertEqual(self.select(tags=entries), 1)

    def test_release_tag_disagreement_blocks_retry(self):
        for release_target, tag_target in ((TARGET, OTHER), (OTHER, TARGET)):
            with self.subTest(release_target=release_target), self.assertRaisesRegex(
                ValueError, "disagree"
            ):
                self.select([release(1, release_target)], [tag(1, tag_target)])

    def test_conflicting_duplicate_revisions_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "conflicting duplicate"):
            self.select([release(2, TARGET), release(2, OTHER)])

    def test_invalid_inventories_cannot_be_interpreted_as_empty_history(self):
        for releases, tags in ((None, []), ({}, []), ([], {}), ([None], []),
                               ([], [{}]), ([], [{"name": f"v{VERSION}-1"}]),
                               ([{"tag_name": f"v{VERSION}-1"}], []),
                               ([], [{"name": f"v{VERSION}-1", "commit": None}])):
            with self.subTest(releases=releases, tags=tags), self.assertRaises(ValueError):
                select_release_revision(VERSION, TARGET, releases, tags)

    def test_invalid_version_and_source_sha_are_rejected(self):
        for version, target in (("155.0", TARGET), ("155x0x8059x79", TARGET),
                                (VERSION, "main"), (VERSION, "a" * 39)):
            with self.subTest(version=version, target=target), self.assertRaises(ValueError):
                select_release_revision(version, target, [], [])


if __name__ == "__main__":
    unittest.main()
