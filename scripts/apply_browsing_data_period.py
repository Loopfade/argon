#!/usr/bin/env python3
"""Make Android deletion dialogs share the existing profile time-period preference.

Review the guarded methods when updating Chromium. An upstream change, including
an upstream implementation of the same behavior, must stop source preparation
instead of silently stacking a second patch on top of it.
"""
import argparse
import hashlib
from pathlib import Path

QUICK_ROOT = Path(
    "chrome/browser/quick_delete/android/java/src/org/chromium/chrome/browser/quick_delete"
)
DIALOG = QUICK_ROOT / "QuickDeleteDialogDelegate.java"
MEDIATOR = QUICK_ROOT / "QuickDeleteMediator.java"
FRAGMENT = Path(
    "chrome/android/java/src/org/chromium/chrome/browser/browsing_data/"
    "ClearBrowsingDataFragment.java"
)
INPUT_PATHS = (DIALOG, MEDIATOR, FRAGMENT)
BRIDGE = Path(
    "chrome/browser/browsing_data/android/java/src/org/chromium/chrome/browser/"
    "browsing_data/BrowsingDataBridge.java"
)
TIME_UTILS = BRIDGE.with_name("TimePeriodUtils.java")
NATIVE_BRIDGE = Path("chrome/browser/android/browsing_data/browsing_data_bridge.cc")
QUICK_BUILD = Path("chrome/browser/quick_delete/BUILD.gn")
API_PATHS = (BRIDGE, TIME_UTILS, NATIVE_BRIDGE, QUICK_BUILD)

DIALOG_SIGNATURE = "    private void updateSpinner(Spinner quickDeleteSpinner) {"
MEDIATOR_SIGNATURE = "    public void onTimePeriodChanged(@TimePeriod int timePeriod) {"
FRAGMENT_SIGNATURE = "    public boolean onPreferenceChange(Preference preference, Object value) {"

IMPORT = "import org.chromium.chrome.browser.browsing_data.TimePeriod;"
BRIDGE_IMPORT = "import org.chromium.chrome.browser.browsing_data.BrowsingDataBridge;\n" + IMPORT
ADAPTER = "        quickDeleteSpinner.setAdapter(adapter);"
REMEMBERED_ADAPTER = """        // ARGON: restore the same profile period used by the full deletion screen.
        @TimePeriod int savedTimePeriod =
                BrowsingDataBridge.getForProfile(
                                assumeNonNull(mTabModelSelector.getCurrentModel().getProfile()))
                        .getBrowsingDataDeletionTimePeriod();
        // Reopening this dialog can leave its previous listener attached.
        // Adapter initialization must not overwrite the saved period with item zero.
        quickDeleteSpinner.setOnItemSelectedListener(null);
        quickDeleteSpinner.setAdapter(adapter);
        int selectedIndex = -1;
        for (int i = 0; i < options.length; i++) {
            if (options[i].getTimePeriod() == savedTimePeriod) {
                selectedIndex = i;
                break;
            }
            if (options[i].getTimePeriod() == TimePeriod.LAST_HOUR) {
                selectedIndex = i;
            }
        }
        assert selectedIndex != -1;
        quickDeleteSpinner.setSelection(selectedIndex);"""
MODEL_PERIOD = "        mPropertyModel.set(QuickDeleteProperties.TIME_PERIOD, timePeriod);"
REMEMBERED_MODEL_PERIOD = MODEL_PERIOD + """
        // ARGON: remember the period independently of confirming data deletion.
        BrowsingDataBridge.getForProfile(mProfile).setBrowsingDataDeletionTimePeriod(timePeriod);"""
FRAGMENT_PERIOD = (
    "            mLastSelectedTimePeriod = ((TimePeriodSpinnerOption) value).getTimePeriod();"
)
REMEMBERED_FRAGMENT_PERIOD = FRAGMENT_PERIOD + """
            // ARGON: cancelling deletion keeps the user's chosen period.
            BrowsingDataBridge.getForProfile(getProfile())
                    .setBrowsingDataDeletionTimePeriod(mLastSelectedTimePeriod);"""


def method_source(text, signature):
    """Extract one reviewed method, including nested anonymous-class methods."""
    if text.count(signature) != 1:
        raise ValueError(f"Expected one Chromium method: {signature.strip()}")
    start = text.index(signature)
    end = text.index("{", start) + 1
    depth = 1
    while depth and end < len(text):
        depth += (text[end] == "{") - (text[end] == "}")
        end += 1
    if depth:
        raise ValueError(f"Incomplete Chromium method: {signature.strip()}")
    return text[start:end]


def replace_once(text, before, after):
    if text.count(after) == 1:
        if before in text.replace(after, "", 1):
            raise ValueError("Ambiguous already-applied browsing-data period patch")
        return text
    if text.count(after) or text.count(before) != 1:
        raise ValueError("Browsing-data period patch anchor changed; review Chromium update")
    return text.replace(before, after, 1)


def reviewed_method(text, signature, digest, before, after):
    method = method_source(text, signature)
    if method.count(after) == 1:
        method = method.replace(after, before, 1)
    if hashlib.sha256(method.encode()).hexdigest() != digest:
        raise ValueError(
            f"Chromium method changed; review browsing-data period patch: {signature.strip()}"
        )
    return replace_once(text, before, after)


def patch_dialog(text):
    text = reviewed_method(
        text, DIALOG_SIGNATURE,
        "574c9e51c1dff0a1921d075389535f004b4c865f959af8a656d838699f8cad66",
        ADAPTER, REMEMBERED_ADAPTER,
    )
    return replace_once(text, IMPORT, BRIDGE_IMPORT)


def patch_mediator(text):
    text = reviewed_method(
        text, MEDIATOR_SIGNATURE,
        "a108ef24e575efeee147d8fa4f9147a678c4cf1d71313882a9548b08656f2662",
        MODEL_PERIOD, REMEMBERED_MODEL_PERIOD,
    )
    return replace_once(text, IMPORT, BRIDGE_IMPORT)


def patch_fragment(text):
    # The existing full-screen reader must continue to use the shared preference.
    reader = (
        "        @TimePeriod int selectedTimePeriod = "
        "browsingDataBridge.getBrowsingDataDeletionTimePeriod();"
    )
    if text.count(reader) != 1:
        raise ValueError("Full deletion screen period reader changed; review Chromium update")
    return reviewed_method(
        text, FRAGMENT_SIGNATURE,
        "1ec9798cfcb916a8d891f929054ae41012bc540c30d9032c3b21f2b700c6e270",
        FRAGMENT_PERIOD, REMEMBERED_FRAGMENT_PERIOD,
    )


def apply(src):
    transforms = ((DIALOG, patch_dialog), (MEDIATOR, patch_mediator),
                  (FRAGMENT, patch_fragment))
    # Check every source before writing any of them.
    writes = {src / path: transform((src / path).read_text())
              for path, transform in transforms}
    for path, content in writes.items():
        if path.read_text() != content:
            path.write_text(content)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--chromium-src", required=True, type=Path)
    apply(parser.parse_args().chromium_src)


if __name__ == "__main__":
    main()
