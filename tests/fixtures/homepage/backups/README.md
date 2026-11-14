# Historical Argon backup fixtures

These files contain synthetic user data exported with the actual `createDump`
and validation code at the immutable revisions below. They are fixed inputs,
not files regenerated with the current serializer during the tests.

| Fixture | Exporter revision | Coverage |
| --- | --- | --- |
| `argon-2026-10-28.json` | `c28701f87f19abab244704f0b35233fbcc4ff981` | Local shortcuts, folders, an empty folder, an uploaded image, preset icons, the same address in two folders, a custom search engine and explicit saved example IDs. |
| `argon-2026-11-13-before-native.json` | `758cd28e772f78ad98615100762a70a2d53a2713` | The same data plus manual shortcut size, AMOLED and a custom background, before BookmarkModel became authoritative. |

Both exporters wrote `app: "argon-start"`, backup `version: 1` and state
`schemaVersion: 2`. Neither file contains `browserBookmarks`, `bookmarkIcons`,
`bookmarksMigrated` or `bookmarkMigrationBackup`. The older file also has no
manual-size or background fields. These are historical file-format variants,
not the older unversioned localStorage profile migration.

The unit checks keep the source files intact, the native scenario runs their
records through the production C++ importer, and Playwright restores them via
the real homepage, re-exports them and checks reload and repeated restoration.
