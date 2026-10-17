"""Guard localized Android resources against stale text and broken placeholders."""
import copy
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import apply_scoped_ca as localization


class RussianTranslationTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.src = Path(directory.name)
        self.source = self.src / "messages.grdp"
        self.source.write_text(
            '<grit-part><message name="IDS_HELLO">'
            'Hello <ph name="USERNAME">%1$s<ex>User</ex></ph>'
            '</message></grit-part>', encoding="utf-8",
        )
        self.catalog = self.src / "messages_ru.xtb"
        self.original = ('<?xml version="1.0" encoding="UTF-8"?>\n'
                         '<translationbundle lang="ru">\n'
                         '  <translation id="123">Существующий перевод</translation>\n'
                         '</translationbundle>\n')
        self.catalog.write_text(self.original, encoding="utf-8")
        self.groups = [{"source": "messages.grdp", "catalog": "messages_ru.xtb",
                        "messages": {"IDS_HELLO": {
                            "english": "Hello USERNAME", "meaning": "",
                            "russian": 'Здравствуйте, <ph name="USERNAME"/>',
                        }}}]
        self.mock_groups = patch.object(localization, "russian_translation_groups",
                                        return_value=self.groups)
        self.mock_groups.start()
        self.addCleanup(self.mock_groups.stop)

    def test_translations_preserve_existing_entries_and_are_idempotent(self):
        writes = localization.russian_translation_writes(self.src)
        self.assertEqual(self.catalog.read_text(), self.original)
        self.catalog.write_text(writes[self.catalog], encoding="utf-8")
        tree = ET.parse(self.catalog)
        self.assertEqual(tree.find("translation[@id='123']").text,
                         "Существующий перевод")
        entry = tree.find("translation[@id='5335090254790956485']")
        self.assertIsNotNone(entry)
        self.assertEqual(entry.find("ph").attrib["name"], "USERNAME")
        self.assertEqual(localization.russian_translation_writes(self.src), writes)

    def test_upstream_text_or_meaning_change_requires_translation_review(self):
        for attribute, value in (("english", "Different USERNAME"),
                                 ("meaning", "different context")):
            with self.subTest(attribute=attribute):
                groups = copy.deepcopy(self.groups)
                groups[0]["messages"]["IDS_HELLO"][attribute] = value
                with patch.object(localization, "russian_translation_groups", return_value=groups):
                    with self.assertRaisesRegex(ValueError, "source changed"):
                        localization.russian_translation_writes(self.src)
                self.assertEqual(self.catalog.read_text(), self.original)

    def test_missing_or_extra_placeholders_are_rejected(self):
        for russian in ("Здравствуйте", '<ph name="OTHER"/>',
                        '<ph name="USERNAME"/><ph name="USERNAME"/>'):
            with self.subTest(russian=russian):
                self.groups[0]["messages"]["IDS_HELLO"]["russian"] = russian
                with self.assertRaisesRegex(ValueError, "placeholders differ"):
                    localization.russian_translation_writes(self.src)

    def test_existing_translation_conflict_is_rejected(self):
        writes = localization.russian_translation_writes(self.src)
        self.catalog.write_text(writes[self.catalog].replace("Здравствуйте", "Привет"))
        with self.assertRaisesRegex(ValueError, "Conflicting Russian translation"):
            localization.russian_translation_writes(self.src)


if __name__ == "__main__":
    unittest.main()
