// Copyright 2026 Argon contributors
// SPDX-License-Identifier: GPL-2.0-only
package org.chromium.chrome.browser.media;

import java.util.List;

/** Runs the production policy directly, with deterministic monotonic timestamps. */
public final class ArgonBackgroundPlaybackPolicyTest {
    private static void check(boolean value, String reason) {
        if (!value) throw new AssertionError(reason);
    }

    private static ArgonBackgroundPlaybackPolicy.Rule rule(String host, int minutes, boolean enabled) {
        return new ArgonBackgroundPlaybackPolicy.Rule(host, minutes, enabled, true);
    }

    public static void main(String[] args) {
        var policy = new ArgonBackgroundPlaybackPolicy();
        check(policy.mayPlay("other.example", false, 0, true), "Off by default");
        var rules = List.of(rule("video.example", 1, true), rule("other.example", 2, true));
        policy.configure(true, rules);
        check(!policy.mayPlay("video.example.attacker.test", false, 0, true), "Domain boundary");
        check(!policy.mayPlay("notvideo.example", false, 0, true), "Not a subdomain");
        check(policy.mayPlay("video.example", true, 0, false), "Foreground is unrestricted");
        check(policy.mayPlay("video.example", false, 1_000, true), "First background session");
        check(policy.mayPlay("www.video.example", false, 60_999, true), "Budget shared with subdomains");
        check(!policy.mayPlay("video.example", false, 61_000, true), "Exact deadline");
        check(!policy.mayPlay("www.video.example", false, 61_001, true), "Second tab cannot renew it");
        check(!policy.mayPlay("video.example", false, 99_000, true), "Pause / Play cannot renew it");
        policy.configure(true, rules);
        check(!policy.mayPlay("video.example", false, 99_000, true), "Saving does not renew it");
        policy.configure(true, List.of());
        check(!policy.mayPlay("video.example", false, 99_000, true), "Deleted site is denied");
        policy.configure(true, rules);
        check(!policy.mayPlay("video.example", false, 99_000, true), "Re-adding does not renew it");
        policy.configure(false, rules);
        check(policy.mayPlay("video.example", false, 99_000, true), "Disabled mode uses browser defaults");
        policy.configure(true, rules);
        check(!policy.mayPlay("video.example", false, 99_000, true), "Re-enabling does not renew it");
        check(policy.mayPlay("other.example", false, 100_000, true), "Independent site budget");
        policy.onForegroundVisit("www.video.example");
        check(policy.mayPlay("video.example", false, 221_000, true), "Actual site visit renews its budget");
        check(!policy.mayPlay("other.example", false, 221_000, true), "Other site's budget stays expired");
        check(!policy.mayPlay("video.example", false, 220_000, true), "Clock rollback fails closed");

        var overrides = new ArgonBackgroundPlaybackPolicy();
        overrides.configure(true, List.of(rule("video.example", 120, true),
                rule("private.video.example", 10, false)));
        check(!overrides.mayPlay("private.video.example", false, 0, true), "Specific disabled rule wins");
        check(overrides.mayPlay("video.example", false, 0, true), "Parent remains permitted");
        check(!overrides.mayPlay("video.example", false, 7_200_000, true), "Two-hour upper bound");
        var exact = new ArgonBackgroundPlaybackPolicy();
        exact.configure(true, List.of(new ArgonBackgroundPlaybackPolicy.Rule("video.example", 1, true, false)));
        check(exact.mayPlay("video.example", false, 0, true), "Exact-host session starts");
        exact.onForegroundVisit("other.video.example");
        check(!exact.mayPlay("video.example", false, 60_000, true), "Unmanaged subdomain cannot renew exact-host rule");
        for (int invalid : new int[] {0, -1, 121, Integer.MAX_VALUE}) {
            boolean rejected = false;
            try { rule("video.example", invalid, true); }
            catch (IllegalArgumentException expected) { rejected = true; }
            check(rejected, "Invalid duration rejected: " + invalid);
        }
        System.out.println("PASS: production playback budgets, domain scope and resets");
    }
}
