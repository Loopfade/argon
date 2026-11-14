"""Restore fixed historical exports through production JS conversion and native import."""
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
BACKUPS = ROOT / 'tests/fixtures/homepage/backups'
FIXTURE = ROOT / 'tests/fixtures/homepage/bookmark_import_flow.cc.in'
spec = importlib.util.spec_from_file_location('homepage_import', ROOT / 'scripts/apply_homepage.py')
homepage = importlib.util.module_from_spec(spec)
spec.loader.exec_module(homepage)


def shape(items):
    return [dict(name=item['name'], type='folder', children=shape(item['children']))
            if 'children' in item else dict(name=item['name'], url=item['url']) for item in items]


@unittest.skipUnless(shutil.which('g++') and shutil.which('node'), 'C++ compiler and Node required')
class HomepageBookmarkImportTests(unittest.TestCase):
    def test_historical_backups_restore_empty_folders_aliases_and_order_without_deleting_native_data(self):
        script = '''
import {readFile} from 'node:fs/promises';
import {parseDump} from './chromium_overlay/chrome/browser/resources/argon_home/state.mjs';
import {bookmarkRecords} from './chromium_overlay/chrome/browser/resources/argon_home/browser-bookmarks.mjs';
const result=[];
for(const name of ['argon-2026-10-28.json','argon-2026-11-13-before-native.json']){
 const dump=parseDump(await readFile('tests/fixtures/homepage/backups/'+name,'utf8'));
 result.push({name,records:bookmarkRecords(dump.state.shortcuts),shortcuts:dump.state.shortcuts});
}
process.stdout.write(JSON.stringify(result));
'''
        converted = subprocess.run(['node', '--input-type=module', '-e', script], cwd=ROOT,
                                   check=True, capture_output=True, text=True)
        cases = json.loads(converted.stdout)
        source = (homepage.OVERLAY / homepage.UI / 'argon_bookmarks_handler.cc').read_text()
        methods = '\n'.join(homepage.method_source(source, signature) for signature in (
            'const bookmarks::BookmarkNode* Destination()', 'void CollectUrls(', 'void Import(',
        ))
        program = FIXTURE.read_text().replace('ARGON_BOOKMARK_IMPORT_METHODS', methods)
        generated = []
        quote = lambda value: json.dumps(value, ensure_ascii=False)
        for case in cases:
            generated.append('''{
bookmarks::BookmarkModel model;Handler handler(&model);
model.Add(model.mobile,10,false,"Existing","https://existing.example/");
model.Add(model.mobile,11,false,"Hidden","chrome://settings/");
base::ListValue records;
''')
            for record in case['records']:
                row = '{base::DictValue row;row.Set("name",' + quote(record['name']) + ');'
                row += 'row.Set("path",base::ListValue{' + ','.join(
                    'base::Value(' + quote(part) + ')' for part in record['path']) + '});'
                row += ('row.Set("folder",true);' if record.get('folder') else
                        'row.Set("url",' + quote(record['url']) + ');')
                generated.append(row + 'records.emplace_back(std::move(row));}\n')
            existing = [dict(name='Existing', url='https://existing.example/'),
                        dict(name='Hidden', url='chrome://settings/')]
            expected = json.dumps(existing + shape(case['shortcuts']), ensure_ascii=False, separators=(',', ':'))
            generated.append('''
const std::string before=Shape(model.mobile);
prefs.enabled=false;handler.Run(records,true,false);assert(Shape(model.mobile)==before);
prefs.enabled=true;handler.Run(records,true);
''')
            generated.append('const std::string expected=' + quote(expected) + ';\n')
            generated.append('''assert(Shape(model.mobile)==expected);
handler.Run(records,true);assert(Shape(model.mobile)==expected);
base::DictValue fresh;fresh.Set("name","Must not appear");fresh.Set("url","https://atomic.example/");fresh.Set("path",base::ListValue{});
base::DictValue invalid;invalid.Set("name","Invalid");invalid.Set("url","javascript:alert(1)");invalid.Set("path",base::ListValue{});
handler.Run({base::Value(fresh),base::Value(invalid)},true,false);assert(Shape(model.mobile)==expected);
''')
            generated.append('std::cout<<' + quote('PASS: historical native import ' + case['name']) + '<<"\\n";\n}\n')
        program = program.replace('ARGON_LEGACY_BACKUP_CASES', ''.join(generated))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'import.cc'
            binary = Path(directory) / 'import'
            path.write_text(program)
            compiled = subprocess.run(['g++', '-std=c++23', '-Wall', '-Wextra', '-Werror', str(path), '-o', str(binary)],
                                      capture_output=True, text=True)
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            result = subprocess.run([str(binary)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.count('PASS: historical native import'), len(cases))
