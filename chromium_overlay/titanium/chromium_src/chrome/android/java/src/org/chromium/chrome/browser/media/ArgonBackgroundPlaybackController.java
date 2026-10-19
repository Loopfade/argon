// Copyright 2026 Argon contributors
// SPDX-License-Identifier: GPL-2.0-only

package org.chromium.chrome.browser.media;

import android.app.Activity;
import android.app.KeyguardManager;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.content.SharedPreferences;
import android.os.Handler;
import android.os.Looper;
import android.os.PowerManager;
import android.os.SystemClock;

import org.chromium.base.ApplicationStatus;
import org.chromium.base.Callback;
import org.chromium.base.ContextUtils;
import org.chromium.base.supplier.NonNullObservableSupplier;
import org.chromium.build.annotations.NullMarked;
import org.chromium.build.annotations.Nullable;
import org.chromium.chrome.browser.tab.Tab;
import org.chromium.chrome.browser.tab.TabImpl;
import org.chromium.chrome.browser.tab.TabObscuringHandler;
import org.chromium.chrome.browser.tabmodel.TabModelSelector;
import org.chromium.chrome.browser.tabmodel.TabModelSelectorTabObserver;
import org.chromium.content_public.browser.MediaSession;
import org.chromium.content_public.browser.MediaSessionObserver;
import org.chromium.content_public.browser.Page;
import org.chromium.content_public.browser.Visibility;
import org.chromium.content_public.browser.WebContents;
import org.chromium.content_public.browser.WebContentsObserver;
import org.chromium.url.GURL;

import java.util.ArrayList;
import java.util.HashMap;
import java.util.HashSet;
import java.util.Map;
import java.util.Set;

/** Uses Chromium's existing player, audio focus, media notification and lock-screen controls. */
@NullMarked
public final class ArgonBackgroundPlaybackController {
    private static final Map<Activity, ArgonBackgroundPlaybackController> sControllers = new HashMap<>();
    private final Activity mActivity;
    private final Context mContext = ContextUtils.getApplicationContext();
    private final ArgonBackgroundPlaybackStore mStore = ArgonBackgroundPlaybackStore.get();
    private final Handler mHandler = new Handler(Looper.getMainLooper());
    private final Map<Tab, State> mTabs = new HashMap<>();
    private final TabModelSelectorTabObserver mObserver;
    private final TabObscuringHandler mObscuringHandler;
    private final NonNullObservableSupplier<Boolean> mScrimVisible;
    private final TabObscuringHandler.Observer mObscuringObserver = (content, toolbar) -> refresh();
    private final Callback<Boolean> mScrimObserver = visible -> refresh();
    private final @Nullable PowerManager mPower = mContext.getSystemService(PowerManager.class);
    private final @Nullable KeyguardManager mKeyguard = mContext.getSystemService(KeyguardManager.class);
    private boolean mDestroyed;
    private boolean mWasEnabled = mStore.policy.isEnabled();
    private final Runnable mTick = this::refresh;
    private final SharedPreferences.OnSharedPreferenceChangeListener mPreferences =
            (preferences, key) -> {
                mStore.reload();
                if (mWasEnabled && !mStore.policy.isEnabled()) {
                    for (State state : mTabs.values()) {
                        if (!isForeground(state.tab)) pause(state);
                    }
                }
                mWasEnabled = mStore.policy.isEnabled();
                refresh();
            };
    private final ApplicationStatus.ActivityStateListener mActivityListener =
            (activity, state) -> {
                refresh();
                mHandler.postDelayed(mTick, 200);
            };
    private final ApplicationStatus.WindowFocusChangedListener mFocusListener;
    private final BroadcastReceiver mReceiver = new BroadcastReceiver() {
        @Override
        public void onReceive(Context context, Intent intent) {
            refresh();
            // Screen-on arrives before keyguard/window focus has necessarily settled.
            mHandler.postDelayed(mTick, 200);
        }
    };
    private static final class State {
        final Tab tab;
        final WebContents contents;
        @Nullable MediaSessionObserver observer;
        @Nullable WebContentsObserver contentsObserver;
        final Set<Integer> players = new HashSet<>();
        boolean sessionPlaying;
        boolean playing;
        boolean wasForeground;
        boolean keepVisible;
        String host = "";

        State(Tab tab, WebContents contents) {
            this.tab = tab;
            this.contents = contents;
        }
    }

    public static void attach(Activity activity, TabModelSelector selector,
            TabObscuringHandler obscuringHandler, NonNullObservableSupplier<Boolean> scrimVisible) {
        if (!sControllers.containsKey(activity)) {
            sControllers.put(activity, new ArgonBackgroundPlaybackController(
                    activity, selector, obscuringHandler, scrimVisible));
        }
    }

    public static void detach(Activity activity) {
        ArgonBackgroundPlaybackController controller = sControllers.remove(activity);
        if (controller != null) controller.destroy();
    }

    /** Called by TabImpl before sending its visibility decision to the renderer. */
    public static @Visibility int resolveVisibility(Tab tab, @Visibility int requested) {
        if (requested == Visibility.VISIBLE || tab.isOffTheRecord() || tab.isDestroyed()) return requested;
        for (var controller : sControllers.values()) {
            State state = controller.mTabs.get(tab);
            if (controller.mDestroyed || state == null || !state.keepVisible || !state.playing
                    || state.contents != tab.getWebContents() || state.contents.isDestroyed()) continue;
            String host = ArgonBackgroundPlaybackStore.hostFromUrl(state.contents.getLastCommittedUrl());
            // A pending/new document cannot inherit the previous host's permission.
            if (host.isEmpty() || !host.equals(state.host)) continue;
            var policy = controller.mStore.policy;
            if (policy.isEnabled() && policy.isSiteEnabled(host)
                    && policy.mayPlay(host, false, SystemClock.elapsedRealtime(), false)) {
                return Visibility.VISIBLE;
            }
        }
        return requested;
    }

    private ArgonBackgroundPlaybackController(Activity activity, TabModelSelector selector,
            TabObscuringHandler obscuringHandler, NonNullObservableSupplier<Boolean> scrimVisible) {
        mActivity = activity;
        mObscuringHandler = obscuringHandler;
        mScrimVisible = scrimVisible;
        mFocusListener = (changedActivity, hasFocus) -> {
            if (changedActivity == mActivity) refresh();
        };
        mObserver = new TabModelSelectorTabObserver(selector) {
            @Override
            protected void onTabRegistered(Tab tab) {
                track(tab);
            }

            @Override
            protected void onTabUnregistered(Tab tab) {
                untrack(tab);
            }

            @Override
            public void onContentChanged(Tab tab) {
                untrack(tab);
                track(tab);
            }

            @Override
            public void onPageLoadStarted(Tab tab, GURL url) {
                track(tab);
                refresh();
            }

            @Override
            public void onPageLoadFinished(Tab tab, GURL url) {
                track(tab);
                refresh();
            }

            @Override
            public void onLoadStopped(Tab tab, boolean toDifferentDocument) {
                refresh();
            }

            @Override
            public void onUrlUpdated(Tab tab) {
                refresh();
            }

            @Override
            public void onInteractabilityChanged(Tab tab, boolean isInteractable) {
                refresh();
            }

            @Override
            public void onDestroyed(Tab tab) {
                untrack(tab);
            }
        };
        IntentFilter filter = new IntentFilter();
        filter.addAction(Intent.ACTION_SCREEN_OFF);
        filter.addAction(Intent.ACTION_SCREEN_ON);
        filter.addAction(Intent.ACTION_USER_PRESENT);
        ContextUtils.registerNonExportedBroadcastReceiver(mContext, mReceiver, filter);
        mStore.preferences.registerOnSharedPreferenceChangeListener(mPreferences);
        ApplicationStatus.registerStateListenerForActivity(mActivityListener, activity);
        ApplicationStatus.registerWindowFocusChangedListener(mFocusListener);
        mObscuringHandler.addObserver(mObscuringObserver);
        mScrimVisible.addSyncObserverAndPostIfNonNull(mScrimObserver);
    }

    private void track(Tab tab) {
        if (mDestroyed || tab.isOffTheRecord() || tab.isDestroyed() || mTabs.containsKey(tab)) return;
        WebContents contents = tab.getWebContents();
        if (contents == null || contents.isDestroyed()) return;
        State state = new State(tab, contents);
        mTabs.put(tab, state);
        // Chromium creates media sessions lazily. Observe creation before querying
        // the existing session so tabs without media remain safe at startup.
        state.contentsObserver = new WebContentsObserver(contents) {
            @Override
            public void mediaSessionCreated(MediaSession session) {
                observeMediaSession(state, session);
            }

            @Override
            public void primaryPageChanged(Page page) {
                if (mTabs.get(tab) != state) return;
                // BFCache/prerender activation need not send a page-load pair.
                refresh();
                if (state.keepVisible) updateNativeVisibility(state);
            }

            @Override
            public void mediaStartedPlaying(int id, boolean hasAudio, boolean hasVideo) {
                if (mTabs.get(tab) != state || (!hasAudio && !hasVideo)) return;
                // Native players can run without a controllable MediaSession.
                state.players.add(id);
                state.playing = true;
                mHandler.post(mTick);
            }

            @Override
            public void mediaStoppedPlaying(int id) {
                if (mTabs.get(tab) != state) return;
                state.players.remove(id);
                state.playing = state.sessionPlaying || !state.players.isEmpty();
                mHandler.post(mTick);
            }

            @Override
            public void webContentsDestroyed() {
                if (mTabs.get(tab) == state) untrack(tab);
            }
        };
        observeMediaSession(state, MediaSession.fromWebContents(contents));
        // Initial registrations arrive in a batch. Enforce only after all tabs,
        // including the foreground tab which can reset their shared budget, exist.
        mHandler.post(mTick);
    }

    private void observeMediaSession(State state, @Nullable MediaSession session) {
        if (session == null || mDestroyed || mTabs.get(state.tab) != state) return;
        MediaSessionObserver previous = state.observer;
        if (previous != null) {
            if (previous.getMediaSession() == session) return;
            previous.stopObserving();
        }
        state.sessionPlaying = false;
        state.playing = !state.players.isEmpty();
        state.observer = new MediaSessionObserver(session) {
            @Override
            public void mediaSessionStateChanged(boolean controllable, boolean suspended) {
                state.sessionPlaying = controllable && !suspended;
                state.playing = state.sessionPlaying || !state.players.isEmpty();
                // Also checks Play from the notification or headphones after a deadline.
                mHandler.post(mTick);
            }

            @Override
            public void mediaSessionDestroyed() {
                state.sessionPlaying = false;
                state.playing = !state.players.isEmpty();
                mHandler.post(mTick);
            }
        };
    }

    private void untrack(Tab tab) {
        State state = mTabs.remove(tab);
        if (state == null) return;
        if (state.observer != null) state.observer.stopObserving();
        if (state.contentsObserver != null) state.contentsObserver.observe(null);
        if (state.keepVisible) updateNativeVisibility(state);
    }

    private static void updateNativeVisibility(State state) {
        // Reuse TabImpl's hidden/occluded/offscreen calculation. Never change
        // Tab.isHidden/isUserInteractable: those still describe a real visit.
        if (!state.tab.isDestroyed() && !state.contents.isDestroyed()
                && state.tab.getWebContents() == state.contents && state.tab instanceof TabImpl tab) {
            tab.refreshBackgroundPlaybackVisibility();
        }
    }

    private boolean isForeground(Tab tab) {
        return mPower != null && mPower.isInteractive()
                && (mKeyguard == null || !mKeyguard.isKeyguardLocked())
                && mActivity.hasWindowFocus() && tab.isUserInteractable()
                && !mObscuringHandler.isTabContentObscured() && !mScrimVisible.get();
    }

    private void updateForegroundVisits() {
        for (State state : new ArrayList<>(mTabs.values())) {
            if (state.contents.isDestroyed() || state.tab.isDestroyed()) continue;
            // A visible/pending address is not the document producing this media.
            GURL url = state.contents.getLastCommittedUrl();
            String host = ArgonBackgroundPlaybackStore.hostFromUrl(url);
            boolean foreground = isForeground(state.tab);
            if (foreground && (!state.wasForeground || !state.host.equals(host))) {
                mStore.policy.onForegroundVisit(host);
            }
            state.wasForeground = foreground;
            state.host = host;
        }
    }

    private void refresh() {
        if (mDestroyed) return;
        mHandler.removeCallbacks(mTick);
        // Budgets are process-wide. Collect real visits in every window before
        // enforcing any tab, independent of map/receiver/registration order.
        for (var controller : new ArrayList<>(sControllers.values())) {
            controller.updateForegroundVisits();
        }
        long now = SystemClock.elapsedRealtime();
        boolean playing = false;
        for (State state : new ArrayList<>(mTabs.values())) {
            if (state.contents.isDestroyed() || state.tab.isDestroyed()) continue;
            String host = state.host;
            boolean allowed = mStore.policy.mayPlay(host, state.wasForeground, now, state.playing);
            if (!allowed && state.playing) pause(state);
            boolean keepVisible = state.playing && mStore.policy.isEnabled()
                    && mStore.policy.isSiteEnabled(host) && allowed;
            if (state.keepVisible != keepVisible) {
                state.keepVisible = keepVisible;
                updateNativeVisibility(state);
            }
            playing |= state.playing;
        }
        // No new wake lock, service, alarm permission or permanent background polling.
        if (playing && mStore.policy.isEnabled()) mHandler.postDelayed(mTick, 1000);
    }

    private void pause(State state) {
        if (state.contents.isDestroyed() || !state.playing) return;
        state.playing = false;
        state.sessionPlaying = false;
        state.players.clear();
        // UI suspend can be intercepted (and ignored) by the site's pause handler.
        // This public Chromium API suspends the media players directly and preserves position.
        state.contents.suspendAllMediaPlayers();
    }

    private void destroy() {
        mDestroyed = true;
        mHandler.removeCallbacksAndMessages(null);
        mObserver.destroy();
        for (Tab tab : new ArrayList<>(mTabs.keySet())) untrack(tab);
        mContext.unregisterReceiver(mReceiver);
        mStore.preferences.unregisterOnSharedPreferenceChangeListener(mPreferences);
        ApplicationStatus.unregisterActivityStateListener(mActivityListener);
        ApplicationStatus.unregisterWindowFocusChangedListener(mFocusListener);
        mObscuringHandler.removeObserver(mObscuringObserver);
        mScrimVisible.removeObserver(mScrimObserver);
    }
}
