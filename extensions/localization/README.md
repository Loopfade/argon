# Russian translation of the bundled Titanium extension

`titanium-0.0.1-ru.patch` is a **source patch**, based on
[Titanium Extension v0.0.1](https://github.com/jqssun/android-titanium-extension/tree/ae324ca7c199348f6c4c20bf15474097900b5b91)
(`ae324ca7c199348f6c4c20bf15474097900b5b91`, GPL-2.0).

Apply it in a checkout of that commit:

```sh
git apply /path/to/argon/extensions/localization/titanium-0.0.1-ru.patch
```

The patch adds standard Chrome extension localization: English remains the
default locale, and Russian follows the browser's UI language. It translates
settings, hints, buttons, the context menu and download dialogs. Permission
declarations, download behavior and marketplace URLs are unchanged. Translation
text is assigned through `textContent`, never `innerHTML`.

The cookies label describes the existing switch accurately: it controls cookies,
while User-Agent and Referer are handled separately by the existing code.

## Integration status

**This patch is not applied to the bundled CRX and is not yet included in APKs.**
The pinned v0.0.1 CRX is signed by Titanium. Changing its contents invalidates
that signature. Shipping these translations requires either a new package signed
with the original extension key, or a separately signed fork with a different
extension ID and a migration plan for existing settings.

The current CRX URL, SHA-256, identity and user settings are preserved. No signing
key is generated or committed by this change.
