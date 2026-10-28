"""Regression for the M155 Android-without-VR missing generated icon header."""
import unittest

from check_upstream_patches import patch, verify_omnibox_icon_dependency


SOURCE = '#include "components/omnibox/browser/vector_icons.h"  // nogncheck\n'
BUILD = '''static_library("browser") {
  sources = [ "searchbox_utils.cc" ]
  if ((!is_android || enable_vr) && !is_ios) {
    deps += [ ":vector_icons" ]
  }

  if (enable_extensions_core) {
    deps += [ "//extensions/common" ]
  }
}

static_library("location_bar") {
  if ((!is_android || enable_vr) && !is_ios) {
    deps += [ ":vector_icons" ]
  }
}
'''


class OmniboxIconTests(unittest.TestCase):
    def test_missing_android_dependency_is_rejected_and_fixed(self):
        with self.assertRaisesRegex(ValueError, "Android without VR"):
            verify_omnibox_icon_dependency(BUILD, SOURCE)
        fixed = patch.patch_omnibox_gn(BUILD)
        verify_omnibox_icon_dependency(fixed, SOURCE)
        self.assertEqual(fixed, patch.patch_omnibox_gn(fixed))
        self.assertEqual(
            BUILD[BUILD.index('static_library("location_bar")'):],
            fixed[fixed.index('static_library("location_bar")'):],
        )

    def test_missing_or_duplicate_build_anchor_is_rejected(self):
        for build in ("", BUILD + BUILD):
            with self.subTest(build=build), self.assertRaises(ValueError):
                patch.patch_omnibox_gn(build)

    def test_other_target_cannot_supply_the_browser_dependency(self):
        fixed = patch.patch_omnibox_gn(BUILD)
        # A matching dependency outside :browser does not fix its include.
        browser, other = fixed.split('static_library("location_bar")', 1)
        browser = browser.replace('if (!is_ios)', 'if (!is_android)')
        other = other.replace('if ((!is_android || enable_vr) && !is_ios)', 'if (!is_ios)')
        with self.assertRaisesRegex(ValueError, "Android without VR"):
            verify_omnibox_icon_dependency(
                browser + 'static_library("location_bar")' + other, SOURCE
            )


if __name__ == "__main__":
    unittest.main()
