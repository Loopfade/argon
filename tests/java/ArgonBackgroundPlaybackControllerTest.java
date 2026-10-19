// Copyright 2026 Argon contributors
// SPDX-License-Identifier: GPL-2.0-only
package org.chromium.chrome.browser.media;

import android.app.Activity;
import android.os.Handler;
import android.os.SystemClock;

import org.chromium.base.ApplicationStatus;
import org.chromium.base.ContextUtils;
import org.chromium.chrome.browser.tab.Tab;
import org.chromium.chrome.browser.tab.TabImpl;
import org.chromium.chrome.browser.tabmodel.TabModelSelector;
import org.chromium.content_public.browser.MediaSession;
import org.chromium.content_public.browser.WebContents;
import org.chromium.content_public.browser.WebContentsObserver;

import java.util.List;

/** Runs the production controller, substituting only Android and Chromium services. */
public final class ArgonBackgroundPlaybackControllerTest {
    private static void check(boolean value, String message) {
        if (!value) throw new AssertionError(message);
    }

    private static MediaSession playingSession() {
        MediaSession session = new MediaSession();
        session.controllable = true;
        return session;
    }

    private static void startupWithoutSession(boolean enabled) {
        SystemClock.now = 0;
        var store = ArgonBackgroundPlaybackStore.get();
        store.policy.configure(enabled,
                List.of(new ArgonBackgroundPlaybackPolicy.Rule("video.example", 1, true, false)));
        Activity activity = new Activity();
        activity.focused = false;
        TabModelSelector selector = new TabModelSelector();
        WebContents contents = new WebContents();
        Tab tab = new TabImpl(contents);
        selector.tabs.add(tab);
        ArgonBackgroundPlaybackController.attach(activity, selector, activity.obscuring, activity.scrim);
        ArgonBackgroundPlaybackController.attach(activity, selector, activity.obscuring, activity.scrim);
        check(contents.observers.size() == 1, "Startup must wait for one session-creation event");
        Handler.runPending();
        check(Handler.pendingCount() == 0, "A tab without media must not start a polling timer");

        // First media can appear long after startup; no page navigation is required.
        SystemClock.now = 120_000;
        MediaSession session = playingSession();
        contents.createSession(session);
        contents.createSession(session);
        check(session.observers.size() == 1, "Repeated creation must not duplicate media observers");
        Handler.runPending();
        check(contents.pauses == 0, "A late first session must receive its full budget");
        SystemClock.now += 60_000;
        Handler.runPending();
        check(contents.pauses == (enabled ? 1 : 0), "Late media must obey the enabled site budget");

        // Detaching removes both observation layers and pending deadline work.
        WebContentsObserver stale = contents.observers.get(0);
        ArgonBackgroundPlaybackController.detach(activity);
        ArgonBackgroundPlaybackController.detach(activity);
        stale.mediaSessionCreated(playingSession());
        check(session.observers.isEmpty(), "Detached session still has a media observer");
        check(contents.observers.isEmpty(), "Detached tab still has a contents observer");
        check(Handler.pendingCount() == 0, "Detached controller still has scheduled work");
    }

    private static void existingAndReplacementSessions() {
        ArgonBackgroundPlaybackStore.get().policy.configure(false, List.of());
        Activity activity = new Activity();
        TabModelSelector selector = new TabModelSelector();
        WebContents contents = new WebContents();
        MediaSession first = playingSession();
        contents.session = first;
        Tab tab = new TabImpl(contents);
        selector.tabs.add(tab);
        ArgonBackgroundPlaybackController.attach(activity, selector, activity.obscuring, activity.scrim);
        check(first.observers.size() == 1, "Existing media must be observed immediately");
        Handler.runPending();

        first.destroy();
        contents.session = null;
        MediaSession second = playingSession();
        contents.createSession(second);
        check(first.observers.isEmpty() && second.observers.size() == 1,
                "A recreated session must replace the destroyed session");
        MediaSession third = playingSession();
        contents.createSession(third);
        check(second.observers.isEmpty() && third.observers.size() == 1,
                "Replacing a live session must detach its old observer");

        WebContentsObserver stale = contents.observers.get(0);
        WebContents replacement = new WebContents();
        tab.contents = replacement;
        selector.observer.onContentChanged(tab);
        check(contents.observers.isEmpty() && third.observers.isEmpty(),
                "Replacing WebContents must release all old observers");
        check(replacement.observers.size() == 1, "Replacement tab must wait for media");
        stale.webContentsDestroyed();
        MediaSession lateOld = playingSession();
        stale.mediaSessionCreated(lateOld);
        check(lateOld.observers.isEmpty(), "Stale contents callback reattached old media");
        MediaSession current = playingSession();
        replacement.createSession(current);
        check(current.observers.size() == 1, "Stale destruction must not untrack replacement contents");

        replacement.destroy();
        check(replacement.observers.isEmpty() && current.observers.isEmpty(),
                "Destroyed contents must release both observer types");
        ArgonBackgroundPlaybackController.detach(activity);
        check(Handler.pendingCount() == 0, "Cleanup left pending callbacks");
    }

    private static void excludedAndClosedTabs() {
        Activity activity = new Activity();
        TabModelSelector selector = new TabModelSelector();
        Tab incognito = new TabImpl(new WebContents());
        incognito.incognito = true;
        Tab frozen = new TabImpl(null);
        WebContents destroyed = new WebContents();
        destroyed.destroyed = true;
        Tab closed = new TabImpl(new WebContents());
        closed.destroyed = true;
        Tab normal = new TabImpl(new WebContents());
        selector.tabs.addAll(List.of(incognito, frozen, new TabImpl(destroyed), closed, normal));
        ArgonBackgroundPlaybackController.attach(activity, selector, activity.obscuring, activity.scrim);
        Handler.runPending();
        check(incognito.contents.observers.isEmpty() && destroyed.observers.isEmpty()
                && closed.contents.observers.isEmpty(), "Excluded tabs must not acquire observers");
        check(normal.contents.observers.size() == 1, "Normal tab was not registered");
        selector.observer.onDestroyed(normal);
        MediaSession later = playingSession();
        normal.contents.createSession(later);
        check(normal.contents.observers.isEmpty() && later.observers.isEmpty(),
                "Closed tab must not acquire observers when media appears later");
        ArgonBackgroundPlaybackController.detach(activity);
    }

    public static void main(String[] args) {
        startupWithoutSession(false);
        startupWithoutSession(true);
        existingAndReplacementSessions();
        excludedAndClosedTabs();
        check(ContextUtils.context.receivers.isEmpty(), "Receiver leaked after controller destruction");
        check(ApplicationStatus.listeners.isEmpty(), "Activity listener leaked after destruction");
        check(ApplicationStatus.focusListeners.isEmpty(), "Focus listener leaked after destruction");
        check(ArgonBackgroundPlaybackStore.get().preferences.listeners.isEmpty(),
                "Preference listener leaked after destruction");
        System.out.println("PASS: production controller startup and media lifecycle");
    }
}
