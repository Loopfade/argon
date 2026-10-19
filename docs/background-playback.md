# Background playback

User-managed background playback for Argon on Android.

## Entry point and user interface

Settings → Background playback / Настройки → Фоновое воспроизведение, in the main
settings list between Appearance and the following advanced settings.

The feature is off by default, with an empty site list. New rules allow playback
and include subdomains by default, with a 60-minute limit. Users can add, edit and
delete up to 100 site rules. Each rule has a domain, an enabled switch, a
subdomain switch and a limit of 1–120 minutes. Tapping the row opens its editor;
the separate switch changes the rule immediately. Editing is saved as one
operation. The list switch changes only the enabled state and preserves newer
duration/subdomain edits made from another settings screen. Unicode domains are
stored canonically and shown using Chromium's security URL formatter. A terminal
DNS root dot, including Unicode dot variants, uses the same canonical key in the
editor, storage and page URL matching. Only HTTPS pages use this mode.

The overview combines the Enable feature / Включить функцию switch and its
state description in one native preference card. How it works / Как это работает
is a separate, focusable help action within that card; opening help does not
toggle the feature. The card explains that background playback includes audio
and video while the browser is minimized or the screen is off. Empty-list
messages distinguish the current enabled state from the effect of enabling an
empty list. No domains are added automatically.

When the feature is off, list rows label permissions as saved settings, rather
than active permissions. With the feature on, rows show their permission,
subdomain scope and overlap count. Allowed rows display their limit in minutes;
blocked rows do not present an unused limit as active. The editor separates
the global feature state from its draft permission and subdomain switches.
Helper text follows both draft switches, and Save explains whether changes
apply now or after the feature is enabled. Detailed help lists overlapping
rules and the most-specific-domain precedence. Exact duplicates and invalid input disable
Save; the final save callback validates again before writing. Unicode input
uses a normal text keyboard. Native GURL and Chromium's private-registry-aware
PSL API reject IPs and whole registries such as co.uk or github.io.

Overlaps are permitted because a child domain can have a different limit or
be a disabled exception. Scope checks honor each subdomain switch, do not
confuse sibling domains or suffix lookalikes, and exclude the original rule
while previewing a rename. Delete confirmation explains the most specific
remaining parent rule and lists retained child rules, so deleting a disabled
exception does not silently appear to block a domain still allowed by a parent.

Russian and English are the only packaged Android UI locales. Other system
languages use the English fallback. Website languages, Unicode support and
the list of languages used for translating web pages are independent.

## Runtime behavior

* The native controller observes ordinary tabs in Chromium's tabbed activity.
  Off-the-record tabs, Custom Tabs and standalone web apps are not modified.
* An enabled mode permits background playback only on the user's enabled sites.
  Disabling the mode pauses its currently backgrounded sessions once, then
  returns to the browser's usual playback behavior.
* The deadline starts with actual background playback, including a native player
  without a controllable media session. An idle or already paused page does not
  consume its first budget merely by leaving the foreground. It is elapsed session time,
  including pauses, rather than a fresh budget for every video or Play action.
  All tabs/windows matching one rule share its deadline.
* Pausing, resuming from the lock screen, reloading, changing video, toggling or
  re-adding a rule does not renew the deadline. A shorter edited duration uses
  the original start time. The duration can never exceed 120 minutes.
* A site's remembered elapsed time follows it when an overlapping child or
  parent rule takes over. Adding a more specific rule or deleting an exception
  therefore cannot restart a live session's clock. A genuine foreground visit
  also resets removed historical rules that covered that site.
* Reset requires an interactive screen, unlocked keyguard, the activity's
  window focus and a user-interactable tab on that site. Waking the screen or
  using another app alone does not reset it. Resetting never starts playback.
* A more specific disabled rule wins over an enabled parent domain rule.
* A monotonic native timer suspends the WebContents' media players directly after
  expiry and checks subsequent MediaSession resume notifications again. This
  bypasses a site's custom pause action handler. Page JavaScript does not grant
  permission or control the budget. Process death stops the player; no browser
  service is started to resurrect it automatically.
* The usual Chromium media notification, metadata, lock-screen controls and
  audio focus remain attached to the site's original player.
* Headphone disconnection uses Chromium's existing media-session pause handler.
  There is no extra audio-output switch setting, device callback or route polling
  in this mode. Connecting headphones adds no pause of its own.

## Compatibility and verification

For a currently playing, permitted HTTPS page, the controller keeps Chromium's
native WebContents visibility at VISIBLE before TabImpl would send HIDDEN or
OCCLUDED to its renderer. This avoids the page's visibility-change pause handler
without executing JavaScript in the site. Chromium's ordinary evaluateJavaScript
API CHECKs privileged origins and must not be used on arbitrary websites; the
test-only API and global injection switches are not enabled by this feature.

Pausing the last player, expiry, revoking permission or detaching the controller
restores TabImpl's normal hidden/occluded/offscreen-rendering decision. Idle
pages do not acquire this override. The actual tab's hidden/interactable state
is unchanged, so synthetic page visibility cannot count as a foreground visit
and reset a budget. Each decision checks the current committed host, effective
rule and deadline, including on primary-page changes without load callbacks.

This native visibility override also affects the page's ordinary rendering and
background optimizations while playback is permitted. DRM and site-specific
restrictions still require device testing. Nothing downloads or transcodes a
separate audio track, and no bandwidth or battery reduction is guaranteed.

`scripts/apply_background_playback.py` runs after the CA/branding overlay. It
registers its Java/resources, patches the activity lifecycle, TabImpl visibility and menu, and
limits packaged UI locales. Changed upstream anchors abort preparation. The
prepared-image input gate tracks this script to reject stale images.

Validation uses the production Java policy with deterministic timestamps, the
actual patched TabImpl visibility decision with adapted window services, a host-side rule-editor regression with the
AndroidX row binding contract, matching Russian/English resource keys and
placeholders, and the pinned-source integration with repeated overlay
application. The editor check covers opening, confirming and cancelling the
domain dialog, adding, editing, restoring and saving a site with the master
mode both enabled and disabled. The domain dialog explicitly selects the
AndroidX edit layout and both buttons because Chromium's preference theme does
not supply the EditTextPreference dialog style. With `seekBarPreferenceStyle`
also absent, the time-limit row explicitly uses AndroidX's Material seekbar
layout through Chromium's merged
`org.chromium.chrome.R`. The host compile check exposes this resource namespace
without an artificial `androidx.preference.R`.

The visibility bridge is compiled in `org.chromium.chrome.browser.tab`, beside
Chromium's package-private `TabImpl`. The media controller uses only the public
`Tab` interface and bridge. Host adapters retain that interface/class boundary;
a negative javac check rejects a direct import of `TabImpl` from the media
package. The pinned-source check verifies those declarations as well as the
bridge's Java source registration, including repeated overlay application.

`python3 tests/check_background_playback_flow.py` runs the production settings,
store, policy and controller together. It uses Android's standalone JSON library
and the pinned AndroidX edit-dialog, seekbar, switch and two-state preference
source without modifications, including real input-confirmation, slider and
switch callbacks. The merged overview layout preserves AndroidX's switchWidget
ID and the native title/summary IDs. Its embedded switch does not consume row
clicks, while the help action handles its own clicks and keyboard focus.
Dependency downloads are
verified by SHA-256, and Chromium's locked Preference version must match the
reviewed fixture. CI runs this check on every code change.

The flow check covers both master modes, invalid and duplicate input, drafts,
restoration, the 100-site cap, confirmed deletion, stored JSON and store
recreation. Saved edits and list switches drive preference notifications into
live controllers, checking multiple windows, specific disabled rules, screen
and keyguard states, deadlines, shortening limits and deletion/re-adding without
renewing the budget. Negative controls reproduce the original seekbar and
missing-EditText failures in the actual AndroidX classes.
Both UI languages and master modes cover the overview and per-domain
descriptions, draft validation, parent/child intersections, excluded rename
sources, disabled exceptions and deletion fallback. Native policy and live
controller checks verify that reassigning an overlapping rule preserves elapsed
time and that only a genuine foreground visit renews the budget.
Overview checks also verify that binding and help do not write preferences,
native row clicks toggle the feature once, first-run defaults are unchanged,
and every editor switch combination has matching helper text. Resource catalog
checks cover upgrading an already prepared image and repeated overlay application.
The flow's WebContents adapter rejects JavaScript evaluation on HTTP(S) origins
instead of silently accepting it. Negative controls exercise that guard, and
the pinned-source check verifies the real Android/native CHECK path. Navigation
in both modes, BFCache-style primary-page changes, visibility restoration,
occlusion, offscreen rendering, paused players and the native deadline are
checked without bypassing Chromium's origin restrictions.

Android/Chromium services, including native GURL, remain host adapters in that
flow check. These checks do not replace an Android build or on-device testing
of dialogs, screen locks, Bluetooth/wired headsets, media notifications and
process death.
