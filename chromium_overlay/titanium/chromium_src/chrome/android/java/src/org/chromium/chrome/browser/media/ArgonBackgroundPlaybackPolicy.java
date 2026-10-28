// Copyright 2026 Argon contributors
// SPDX-License-Identifier: GPL-2.0-only

package org.chromium.chrome.browser.media;

import java.util.ArrayList;
import java.util.Comparator;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

/** Process-wide, monotonic budgets. No page script or wall clock controls these deadlines. */
public final class ArgonBackgroundPlaybackPolicy {
    public static final int MAX_MINUTES = 120;
    public static final int MAX_SITES = 100;

    public static final class Rule {
        public final String host;
        public final int minutes;
        public final boolean enabled;
        public final boolean subdomains;

        public Rule(String host, int minutes, boolean enabled, boolean subdomains) {
            if (host.isEmpty() || minutes < 1 || minutes > MAX_MINUTES) {
                throw new IllegalArgumentException("Invalid background playback rule");
            }
            this.host = host;
            this.minutes = minutes;
            this.enabled = enabled;
            this.subdomains = subdomains;
        }

        boolean matches(String candidate) {
            return candidate.equals(host) || (subdomains && candidate.endsWith("." + host));
        }
    }

    private boolean mEnabled;
    private final List<Rule> mRules = new ArrayList<>();
    // Shared by all tabs/windows, including when the user pauses or changes a video.
    private final Map<String, Long> mStartedAt = new HashMap<>();
    // A live site's elapsed time follows it when a parent/child rule takes over.
    private final Map<String, Long> mHostStartedAt = new HashMap<>();
    private final Map<String, Rule> mBudgetScopes = new HashMap<>();

    public boolean isEnabled() {
        return mEnabled;
    }

    public void configure(boolean enabled, List<Rule> rules) {
        if (rules.size() > MAX_SITES) throw new IllegalArgumentException("Too many sites");
        mEnabled = enabled;
        mRules.clear();
        mRules.addAll(rules);
        for (Rule rule : rules) {
            if (mStartedAt.containsKey(rule.host)) mBudgetScopes.put(rule.host, rule);
        }
        // Editing, disabling, deleting or re-adding a rule must not renew a live budget.
    }

    public List<Rule> getRules() {
        return new ArrayList<>(mRules);
    }

    public boolean isSiteEnabled(String host) {
        int index = findRule(host);
        return index >= 0 && mRules.get(index).enabled;
    }

    /** Describe real scope intersections, including disabled exceptions. */
    public List<Rule> getOverlappingRules(Rule candidate, String replacedHost) {
        List<Rule> result = new ArrayList<>();
        for (Rule rule : mRules) {
            if (rule.host.equals(candidate.host) || rule.host.equals(replacedHost)) continue;
            if (rule.matches(candidate.host) || candidate.matches(rule.host)) result.add(rule);
        }
        result.sort(Comparator.comparingInt((Rule rule) -> rule.host.length()).reversed());
        return result;
    }

    /** The first entry is the rule that will apply after removing replacedHost. */
    public List<Rule> getCoveringRules(String host, String replacedHost) {
        List<Rule> result = new ArrayList<>();
        for (Rule rule : mRules) {
            if (!rule.host.equals(replacedHost) && rule.matches(host)) result.add(rule);
        }
        result.sort(Comparator.comparingInt((Rule rule) -> rule.host.length()).reversed());
        return result;
    }

    /** A more specific rule, even a disabled one, takes precedence over its parent. */
    private int findRule(String host) {
        int result = -1;
        for (int i = 0; i < mRules.size(); i++) {
            Rule rule = mRules.get(i);
            if (rule.matches(host)
                    && (result < 0 || rule.host.length() > mRules.get(result).host.length())) {
                result = i;
            }
        }
        return result;
    }

    /** Call only after verifying screen, keyguard, activity focus and the selected tab. */
    public void onForegroundVisit(String host) {
        int index = findRule(host);
        if (index >= 0) {
            Rule rule = mRules.get(index);
            mStartedAt.remove(rule.host);
            mBudgetScopes.remove(rule.host);
            mHostStartedAt.entrySet().removeIf(entry -> rule.matches(entry.getKey()));
        } else {
            mHostStartedAt.remove(host);
        }
        // A genuine visit also resets removed rules that used to cover this site.
        // Active, more general/specific rules keep their own independent budgets.
        var budgets = mBudgetScopes.entrySet().iterator();
        while (budgets.hasNext()) {
            var entry = budgets.next();
            Rule scope = entry.getValue();
            if (scope.matches(host) && mRules.stream().noneMatch(rule -> rule.host.equals(scope.host))) {
                mStartedAt.remove(scope.host);
                mHostStartedAt.entrySet().removeIf(item -> scope.matches(item.getKey()));
                budgets.remove();
            }
        }
    }

    public boolean mayPlay(String host, boolean foreground, long now, boolean startBudget) {
        if (foreground) return true;
        if (!mEnabled) return false;
        int index = findRule(host);
        if (index < 0 || !mRules.get(index).enabled) return false;
        Rule rule = mRules.get(index);
        Long started = mStartedAt.get(rule.host);
        Long hostStarted = mHostStartedAt.get(host);
        if (hostStarted != null && (started == null || hostStarted < started)) started = hostStarted;
        if (started == null) {
            if (!startBudget) return true;
            started = now;
        }
        mStartedAt.put(rule.host, started);
        mBudgetScopes.put(rule.host, rule);
        if (startBudget) mHostStartedAt.put(host, started);
        return now >= started && now - started < rule.minutes * 60_000L;
    }
}
