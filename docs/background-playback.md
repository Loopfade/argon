# Background playback

User-managed background playback for Argon on Android.

## Entry point and user interface

Settings → Background playback / Настройки → Воспроизведение в фоне, in the main
settings list between Appearance and the following advanced settings.

The mode is off by default, with an empty site list. Users can add, edit and
delete up to 100 site rules. Each rule has a domain, an enabled switch, a
subdomain switch and a limit of 1–120 minutes. Tapping the row opens its editor;
the separate switch changes the rule immediately. Editing is saved as one
operation. Unicode domains are stored canonically and shown using Chromium's
security URL formatter. Only HTTPS pages use this mode.

Russian and English are the only packaged Android UI locales. Other system
languages use the English fallback. Website languages, Unicode support and
the list of languages used for translating web pages are independent.

## Runtime behavior

* The native controller observes ordinary tabs in Chromium's tabbed activity.
  Off-the-record tabs, Custom Tabs and standalone web apps are not modified.
* An enabled mode permits background playback only on the user's enabled sites.
  Disabling the mode pauses its currently backgrounded sessions once, then
  returns to the browser's usual playback behavior.
* The deadline starts when a site's controllable media session leaves the
  foreground, including when the screen locks. It is elapsed session time,
  including pauses, rather than a fresh budget for every video or Play action.
  All tabs/windows matching one rule share its deadline.
* Pausing, resuming from the lock screen, reloading, changing video, toggling or
  re-adding a rule does not renew the deadline. A shorter edited duration uses
  the original start time. The duration can never exceed 120 minutes.
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

A small independently implemented, reversible Page Visibility compatibility
shim is injected only for enabled user sites. It does not change Chromium's
actual native page visibility or disable its existing background video-track
optimization. It runs after page load and covers the main document; early
handlers, cross-origin embedded players, DRM and site-specific restrictions
still require device testing. Nothing downloads or transcodes a separate audio
track, and no bandwidth or battery reduction is guaranteed for multiplexed A/V.

`scripts/apply_background_playback.py` runs after the CA/branding overlay. It
registers its Java/resources, patches the activity lifecycle and menu, and
limits packaged UI locales. Changed upstream anchors abort preparation. The
prepared-image input gate tracks this script to reject stale images.

Validation uses the production Java policy with deterministic timestamps, a
JavaScript compatibility test, matching Russian/English resource keys and
placeholders, and the pinned-source integration with repeated overlay
application. These checks do not replace an Android build or on-device testing
of screen locks, Bluetooth/wired headsets, media notifications and process death.
