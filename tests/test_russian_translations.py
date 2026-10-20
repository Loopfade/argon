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
        self.mock_bundles = patch.object(localization, "russian_resource_bundles", return_value=[])
        self.mock_bundles.start()
        self.addCleanup(self.mock_bundles.stop)

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

    def test_reviewed_nontranslatable_message_is_enabled_and_rebranded(self):
        self.source.write_text(
            '<grit-part><message name="IDS_HELLO" translateable="false">'
            'Open in Incognito Titanium</message>'
            '<message name="IDS_UNTOUCHED" translateable="false">Other</message>'
            '</grit-part>'
        )
        self.groups[0]["messages"]["IDS_HELLO"] = {
            "english": "Open in Incognito Titanium", "meaning": "",
            "english_replacement": "Open in Incognito Argon",
            "enable_translation": True, "russian": "Открыть инкогнито в Argon",
        }
        original = self.source.read_text()
        writes = localization.russian_translation_writes(self.src)
        self.assertEqual(self.source.read_text(), original)
        tree = ET.fromstring(writes[self.source])
        message = tree.find("message[@name='IDS_HELLO']")
        self.assertEqual(message.attrib["translateable"], "true")
        self.assertEqual(message.text, "Open in Incognito Argon")
        self.assertEqual(tree.find("message[@name='IDS_UNTOUCHED']").attrib["translateable"], "false")
        translations = ET.fromstring(writes[self.catalog])
        identifier = localization.grit_message_id("Open in Incognito Argon")
        self.assertEqual(translations.find(f"translation[@id='{identifier}']").text,
                         "Открыть инкогнито в Argon")
        old_id = localization.grit_message_id("Open in Incognito Titanium")
        self.assertIsNone(translations.find(f"translation[@id='{old_id}']"))
        for path, text in writes.items():
            path.write_text(text)
        for path, text in localization.russian_translation_writes(self.src).items():
            self.assertEqual(path.read_text(), text)

    def test_nontranslatable_placeholder_message_keeps_its_parameters(self):
        self.source.write_text(self.source.read_text().replace(
            '<message name="IDS_HELLO">',
            '<message name="IDS_HELLO" translateable="false">',
        ))
        self.groups[0]["messages"]["IDS_HELLO"]["enable_translation"] = True
        writes = localization.russian_translation_writes(self.src)
        message = ET.fromstring(writes[self.source]).find("message")
        self.assertEqual(message.attrib["translateable"], "true")
        self.assertEqual(message.find("ph").text, "%1$s")
        self.assertEqual(message.find("ph/ex").text, "User")
        translation = ET.fromstring(writes[self.catalog]).find(
            "translation[@id='5335090254790956485']"
        )
        self.assertEqual(translation.find("ph").attrib["name"], "USERNAME")

    def test_only_reviewed_existing_russian_text_can_be_replaced(self):
        identifier = "5335090254790956485"
        self.catalog.write_text(self.original.replace(
            "</translationbundle>",
            f'<translation id="{identifier}">Hello <ph name="USERNAME"/></translation>'
            '</translationbundle>',
        ))
        self.groups[0]["messages"]["IDS_HELLO"]["previous_russian"] = "Hello USERNAME"
        writes = localization.russian_translation_writes(self.src)
        tree = ET.fromstring(writes[self.catalog])
        self.assertEqual(len(tree.findall(f"translation[@id='{identifier}']")), 1)
        self.assertEqual(tree.find(f"translation[@id='{identifier}']").text, "Здравствуйте, ")
        self.assertEqual(tree.find("translation[@id='123']").text, "Существующий перевод")
        self.catalog.write_text(writes[self.catalog])
        self.assertEqual(localization.russian_translation_writes(self.src), writes)
        self.catalog.write_text(writes[self.catalog].replace("Здравствуйте", "Другой перевод"))
        with self.assertRaisesRegex(ValueError, "Conflicting Russian translation"):
            localization.russian_translation_writes(self.src)

    def test_source_changes_reject_stale_or_ambiguous_messages(self):
        self.groups[0]["messages"]["IDS_HELLO"]["enable_translation"] = True
        source = self.source.read_text().replace(
            '<message name="IDS_HELLO">',
            '<message name="IDS_HELLO" translateable="false">',
        )
        for changed in (source.replace("Hello", "Goodbye"), source + source,
                        source.replace(' translateable="false"', "")):
            with self.subTest(source=changed):
                self.source.write_text(changed)
                with self.assertRaises(ValueError):
                    localization.russian_translation_writes(self.src)

    def test_branding_reconnects_legacy_russian_ids_and_keeps_the_original(self):
        self.source.write_text(
            '<grit-part><message name="IDS_BRANDING">Open Titanium settings</message></grit-part>'
        )
        original_id = localization.grit_message_id("Open Chromium settings")
        current_id = localization.grit_message_id("Open Titanium settings")
        self.catalog.write_text(
            '<translationbundle lang="ru">'
            f'<translation id="{original_id}">Открыть настройки Titanium</translation>'
            '</translationbundle>'
        )
        bundles = [{"catalog": "messages_ru.xtb", "sources": ["messages.grdp"]}]
        with patch.object(localization, "russian_translation_groups", return_value=[]), \
             patch.object(localization, "russian_resource_bundles", return_value=bundles):
            writes = localization.russian_translation_writes(self.src)
            self.catalog.write_text(writes[self.catalog])
            self.assertEqual(localization.russian_translation_writes(self.src), writes)
        tree = ET.parse(self.catalog)
        self.assertEqual(tree.find(f"translation[@id='{original_id}']").text,
                         "Открыть настройки Titanium")
        self.assertEqual(tree.find(f"translation[@id='{current_id}']").text,
                         "Открыть настройки Argon")

    def test_branding_does_not_guess_between_different_russian_translations(self):
        self.source.write_text(
            '<grit-part><message name="IDS_BRANDING">Open Titanium settings</message></grit-part>'
        )
        entries = [(localization.grit_message_id("Open Chrome settings"), "Открыть настройки"),
                   (localization.grit_message_id("Open Chromium settings"), "Перейти к настройкам")]
        self.catalog.write_text('<translationbundle lang="ru">' + ''.join(
            f'<translation id="{identifier}">{value}</translation>'
            for identifier, value in entries
        ) + '</translationbundle>')
        bundles = [{"catalog": "messages_ru.xtb", "sources": ["messages.grdp"]}]
        with patch.object(localization, "russian_translation_groups", return_value=[]), \
             patch.object(localization, "russian_resource_bundles", return_value=bundles):
            with self.assertRaisesRegex(ValueError, "Ambiguous branding translation"):
                localization.russian_translation_writes(self.src)

    def test_brand_names_and_multiline_message_identity_are_preserved(self):
        self.assertEqual(localization.russian_browser_name(
            "Интернет-магазина Titanium; Titanium Web Store; Titanium Root Store; "
            "Titanium Root Program; Titanium OS Flex; Titanium Enterprise Connectors; "
            "браузер Titanium"),
            "Интернет-магазина Chrome; Chrome Web Store; Chrome Root Store; "
            "Chrome Root Program; ChromeOS Flex; Chrome Enterprise Connectors; "
            "браузер Argon")
        message = ET.fromstring('<message>One line\nsecond line</message>')
        self.assertEqual(localization.presentable_message(message), "One line\nsecond line")


if __name__ == "__main__":
    unittest.main()
