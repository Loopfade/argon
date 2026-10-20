# Russian Android settings

`ru.json` adds translations for downstream settings, the restored local
password manager and missing Android messages to Chromium's Russian XTB
catalogs. Conditional variants can share a message name; each variant records
its exact English text and meaning. Other source catalogs keep their upstream
translations; Android packages only English and Russian UI resources through
`scripts/apply_background_playback.py`.

`scripts/apply_scoped_ca.py` validates the pinned English text, message meaning
and placeholder names before updating any files. The message IDs follow
Chromium GRIT's fingerprint algorithm; existing translations are preserved and
conflicting values fail preparation. Reapplying the overlay is idempotent.

Reviewed entries can set `enable_translation` to make a message marked
`translateable="false"` available to GRIT translations. `english_replacement`
rebrands a plain-text source message after validating its original text; the
incognito menu uses Argon in both English and Russian. Existing Russian entries
can only be replaced when `previous_russian` matches their reviewed old text.
All placeholders and unrelated messages remain intact. These resource changes
do not enable the experimental features that display some of the messages.

`bundles.json` lists the pinned resource bundles and all their included source
files. Vanadium and Titanium rename the browser in the English messages and
Russian catalogs, but leave the catalogs' numeric IDs based on the original
Chrome/Chromium English. The overlay recovers these existing Russian messages
under the IDs of the renamed source. Original IDs remain available. Different
candidate translations require an explicit reviewed entry in `ru.json`.
Browser references use Argon; upstream products such as Chrome Web Store and
Chrome Root Store retain their names.

Android home-screen widget names are translated separately in
`chromium_overlay/titanium/chromium_src/chrome/android/java/res/values-ru/argon_widget_strings.xml`.
The bundled extension has its own source-only localization patch described in
`extensions/localization/README.md`; these native resource changes do not
replace its signed CRX.

When updating Chromium or Titanium, review changed English text and meaning
before updating the corresponding entry. Keep all `<ph name="..."/>` placeholders,
including repeated placeholders. The upstream integration check applies the
translations to the pinned patched sources in CI.

Translating a conditional string does not enable the corresponding feature.
