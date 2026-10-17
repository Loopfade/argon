# Russian Android settings

`ru.json` adds translations for downstream settings and the restored local
password manager to Chromium's existing Russian XTB catalogs. Other languages
continue to use their upstream translations.

`scripts/apply_scoped_ca.py` validates the pinned English text, message meaning
and placeholder names before updating any files. The message IDs follow
Chromium GRIT's fingerprint algorithm; existing translations are preserved and
conflicting values fail preparation. Reapplying the overlay is idempotent.

When updating Chromium or Titanium, review changed English text and meaning
before updating the corresponding entry. Keep all `<ph name="..."/>` placeholders,
including repeated placeholders. The upstream integration check applies the
translations to the pinned patched sources in CI.

Translating a conditional string does not enable the corresponding feature.
