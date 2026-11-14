"""Run the pinned Chromium preference traversal and matching on the patched settings XML."""
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
OVERLAY = ROOT / 'chromium_overlay/titanium/chromium_src/chrome/android/java'
FIXTURE = ROOT / 'tests/fixtures/external_navigation/SettingsSearchFlow.java.in'


def verify_search(src):
    import apply_external_navigation as patch
    from apply_homepage import method_source

    main = (src / patch.MAIN_PREFERENCES).read_text()
    if 'android:key="argon_external_navigation"' in main:
        raise ValueError('External navigation must not remain in the main settings list')
    privacy = (src / patch.PRIVACY_PREFERENCES).read_text()
    if privacy.count(patch.SECURITY_MENU_ENTRY) != 1:
        raise ValueError('Missing or duplicate external navigation Security entry')
    provider = (src / patch.PRIVACY_SETTINGS).read_text()
    if not re.search(r'new ChromeBaseSearchIndexProvider\(\s*PrivacySettings.class.getName\(\), R.xml.privacy_preferences\)', provider):
        raise ValueError('Review the searchable PrivacySettings XML provider')
    if 'PrivacySettings.SEARCH_INDEX_DATA_PROVIDER' not in (src / patch.SEARCH_REGISTRY).read_text():
        raise ValueError('Privacy settings must be registered for settings search')
    parser = (src / patch.PREFERENCE_PARSER).read_text()
    for call in ('.setHeader(bundle.getString(METADATA_HEADER))',
                 '.setFragment(bundle.getString(METADATA_FRAGMENT))',
                 '.setKeywords(bundle.getString(METADATA_KEYWORDS))',
                 '.setIsSearchable(isSearchable)'):
        if call not in parser:
            raise ValueError('Review the pinned preference-to-search mapping: ' + call)
    search = (src / patch.SEARCH_INDEX).read_text()
    constants = '\n'.join(re.findall(r'(?:public|private) static final (?:int|Pattern) (?:EXACT_TITLE_MATCH|PARTIAL_TITLE_MATCH|PARTIAL_SUMMARY_MATCH|PARTIAL_KEYWORD_MATCH|STRIP_ACCENTS_PATTERN) = [^;]+;', search))
    parser_constants = '\n'.join(re.findall(r'public static final String METADATA_\w+ = [^;]+;', parser))
    methods = '\n'.join((method_source(parser, 'public static List<Bundle> parsePreferences('),
                         method_source(search, 'private static @Nullable String normalizeString('),
                         method_source(search, 'public static class SearchResults '),
                         method_source(search, 'public SearchResults search(')))
    methods = methods.replace('@Nullable ', '')
    program = FIXTURE.read_text().replace('/* PARSER_CONSTANTS */', parser_constants)
    program = program.replace('/* SEARCH_CONSTANTS */', constants).replace('/* PRODUCTION_METHODS */', methods)
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        source = root / 'SettingsSearchFlow.java'
        source.write_text(program)
        compiler = ['javac'] if shutil.which('javac') else ['java', '-m', 'jdk.compiler/com.sun.tools.javac.Main']
        compiled = subprocess.run([*compiler, '-d', directory, str(source)], capture_output=True, text=True)
        if compiled.returncode:
            raise ValueError(compiled.stdout + compiled.stderr)
        for locale, header, queries in (
            ('values', 'Security', ['Open links in apps', 'LINKS', 'apps', 'external apps']),
            ('values-ru', 'Безопасность', ['Открытие ссылок в приложениях', 'ОТКРЫТИЕ', 'ссылки', 'приложения', 'внешние приложения']),
        ):
            strings = {node.attrib['name']: node.text for node in ET.parse(
                OVERLAY / 'res' / locale / 'argon_external_navigation_strings.xml').getroot()}
            strings['security_section_title'] = header
            catalog = root / (locale + '.properties')
            catalog.write_text('\n'.join(key + '=' + value.replace('\\', '\\\\').replace('\n', '\\n')
                                         for key, value in strings.items()), encoding='utf-8')
            result = subprocess.run(['java', '-ea', '-cp', directory, 'SettingsSearchFlow',
                                     str(src / patch.PRIVACY_PREFERENCES), str(catalog), *queries],
                                    capture_output=True, text=True)
            if result.returncode:
                raise ValueError(result.stdout + result.stderr)
    return 'PASS: pinned settings XML/search find the Security entry in Russian and English'
