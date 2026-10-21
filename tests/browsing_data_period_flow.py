"""Compile reviewed Chromium method bodies with host UI/storage adapters.

The adapters replace Android rendering and native preference I/O. Selection,
initialization and navigation methods are copied verbatim from the source under
test; they are not reimplemented here. This does not replace an Android UI test.
"""
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import apply_browsing_data_period as patch


def run_flow(sources, time_utils):
    java = shutil.which("java")
    if not java:
        raise RuntimeError("JDK required for browsing-data period regression")
    fragment = sources[str(patch.FRAGMENT)]
    start = fragment.index("        // The time range selection spinner.")
    end_anchor = (
        "        mLastSelectedTimePeriod = "
        "((TimePeriodSpinnerOption) spinnerSelection).getTimePeriod();"
    )
    end = fragment.index(end_anchor, start) + len(end_anchor)
    initializer = (
        "    private void initializePeriod() {\n"
        "        BrowsingDataBridge browsingDataBridge = "
        "BrowsingDataBridge.getForProfile(getProfile());\n"
        + fragment[start:end] + "\n    }"
    )
    # Only package/import declarations and the top-level public modifier change
    # when the real utility is compiled into this single-file host harness.
    utility = re.sub(r"^(?:package|import) [^\n]+\n", "", time_utils, flags=re.MULTILINE)
    utility = utility.replace("public class TimePeriodUtils {", "class TimePeriodUtils {", 1)
    methods = {
        "TIME_UTILS": utility,
        "QUICK_SPINNER": patch.method_source(sources[str(patch.DIALOG)], patch.DIALOG_SIGNATURE),
        "QUICK_NAVIGATION": patch.method_source(
            sources[str(patch.DIALOG)], "    private void openClearBrowsingDataDialog() {"
        ),
        "QUICK_SELECTION": patch.method_source(
            sources[str(patch.MEDIATOR)], patch.MEDIATOR_SIGNATURE
        ),
        "FULL_SELECTION": patch.method_source(fragment, patch.FRAGMENT_SIGNATURE),
        "FULL_INDEX": patch.method_source(fragment, "    private int getSpinnerIndex("),
        "FULL_INITIALIZATION": initializer,
    }
    code = (ROOT / "tests/fixtures/BrowsingDataPeriodFlow.java.in").read_text()
    for key, value in methods.items():
        marker = "/* " + key + " */"
        if code.count(marker) != 1:
            raise ValueError(f"Missing host regression insertion: {key}")
        code = code.replace(marker, value)
    with tempfile.TemporaryDirectory(prefix="argon-deletion-period-") as temporary:
        root = Path(temporary)
        source = root / "BrowsingDataPeriodFlow.java"
        source.write_text(code)
        compiler = ([shutil.which("javac")] if shutil.which("javac") else
                    [java, "-m", "jdk.compiler/com.sun.tools.javac.Main"])
        compiled = subprocess.run(
            [*compiler, "-d", str(root / "classes"), str(source)],
            capture_output=True, text=True,
        )
        if compiled.returncode:
            raise RuntimeError("Period regression did not compile:\n"
                               + compiled.stdout + compiled.stderr)
        return subprocess.run(
            [java, "-ea", "-cp", str(root / "classes"),
             "org.chromium.argon.period.BrowsingDataPeriodFlow", str(root)],
            capture_output=True, text=True,
        )


def verify_upstream(src):
    bridge = (src / patch.BRIDGE).read_text()
    for declaration in (
        "public @TimePeriod int getBrowsingDataDeletionTimePeriod()",
        "public void setBrowsingDataDeletionTimePeriod(@TimePeriod int timePeriod)",
    ):
        if declaration not in bridge:
            raise ValueError("Chromium deletion-period bridge API changed")
    native = (src / patch.NATIVE_BRIDGE).read_text()
    getter = patch.method_source(
        native, "static int32_t JNI_BrowsingDataBridge_GetBrowsingDataDeletionTimePeriod("
    )
    setter = patch.method_source(
        native, "static void JNI_BrowsingDataBridge_SetBrowsingDataDeletionTimePeriod("
    )
    if ("GetPrefService(profile)->GetInteger(" not in getter
            or "browsing_data::GetTimePeriodPreferenceName()" not in getter
            or "browsing_data::GetTimePeriodPreferenceName()" not in setter
            or "prefs->SetInteger(pref_name, time_period)" not in setter):
        raise ValueError("Chromium no longer stores the period in the reviewed profile preference")
    if '"//chrome/browser/browsing_data/android:java"' not in (src / patch.QUICK_BUILD).read_text():
        raise ValueError("Quick Delete no longer depends on the browsing-data bridge module")
    result = run_flow({str(path): (src / path).read_text() for path in patch.INPUT_PATHS},
                      (src / patch.TIME_UTILS).read_text())
    if result.returncode:
        raise ValueError("Pinned Chromium deletion-period flow failed:\n"
                         + result.stdout + result.stderr)
    print(result.stdout.strip())
