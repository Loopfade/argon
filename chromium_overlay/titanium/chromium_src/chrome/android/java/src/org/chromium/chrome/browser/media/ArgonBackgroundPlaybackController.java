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
import org.chromium.base.ContextUtils;
import org.chromium.build.annotations.NullMarked;
import org.chromium.build.annotations.Nullable;
import org.chromium.chrome.R;
import org.chromium.chrome.browser.tab.Tab;
import org.chromium.chrome.browser.tabmodel.TabModelSelector;
import org.chromium.chrome.browser.tabmodel.TabModelSelectorTabObserver;
import org.chromium.content_public.browser.MediaSession;
import org.chromium.content_public.browser.MediaSessionObserver;
import org.chromium.content_public.browser.WebContents;
import org.chromium.content_public.browser.WebContentsObserver;
import org.chromium.url.GURL;

import java.io.BufferedReader;
import java.io.IOException;
import java.io.InputStreamReader;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.Map;

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
    private final @Nullable PowerManager mPower = mContext.getSystemService(PowerManager.class);
    private final @Nullable KeyguardManager mKeyguard = mContext.getSystemService(KeyguardManager.class);
    private final String mScript;
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
        boolean controllable;
        boolean playing;
        boolean wasForeground;
        boolean documentReady = true;
        @Nullable Boolean override;
        String host = "";

        State(Tab tab, WebContents contents) {
            this.tab = tab;
            this.contents = contents;
        }
    }

    public static void attach(Activity activity, TabModelSelector selector) {
        if (!sControllers.containsKey(activity)) {
            sControllers.put(activity, new ArgonBackgroundPlaybackController(activity, selector));
        }
    }

    public static void detach(Activity activity) {
        ArgonBackgroundPlaybackController controller = sControllers.remove(activity);
        if (controller != null) controller.destroy();
    }

    private ArgonBackgroundPlaybackController(Activity activity, TabModelSelector selector) {
        mActivity = activity;
        mScript = readScript();
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
                State state = mTabs.get(tab);
                if (state != null) {
                    state.documentReady = false;
                    state.override = null;
                }
            }

            @Override
            public void onPageLoadFinished(Tab tab, GURL url) {
                track(tab);
                State state = mTabs.get(tab);
                if (state != null) state.documentReady = true;
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
            public void webContentsDestroyed() {
                if (mTabs.get(tab) == state) untrack(tab);
            }
        };
        observeMediaSession(state, MediaSession.fromWebContents(contents));
        refresh();
    }

    private void observeMediaSession(State state, @Nullable MediaSession session) {
        if (session == null || mDestroyed || mTabs.get(state.tab) != state) return;
        MediaSessionObserver previous = state.observer;
        if (previous != null) {
            if (previous.getMediaSession() == session) return;
            previous.stopObserving();
        }
        state.playing = false;
        state.controllable = false;
        state.observer = new MediaSessionObserver(session) {
            @Override
            public void mediaSessionStateChanged(boolean controllable, boolean suspended) {
                state.controllable = controllable;
                state.playing = controllable && !suspended;
                // Also checks Play from the notification or headphones after a deadline.
                mHandler.post(mTick);
            }

            @Override
            public void mediaSessionDestroyed() {
                state.playing = false;
                state.controllable = false;
                mHandler.post(mTick);
            }
        };
    }

    private void untrack(Tab tab) {
        State state = mTabs.remove(tab);
        if (state == null) return;
        if (state.observer != null) state.observer.stopObserving();
        if (state.contentsObserver != null) state.contentsObserver.observe(null);
    }

    private boolean isForeground(Tab tab) {
        return mPower != null && mPower.isInteractive()
                && (mKeyguard == null || !mKeyguard.isKeyguardLocked())
                && mActivity.hasWindowFocus() && tab.isUserInteractable();
    }

    private void refresh() {
        if (mDestroyed) return;
        mHandler.removeCallbacks(mTick);
        long now = SystemClock.elapsedRealtime();
        boolean playing = false;
        for (State state : new ArrayList<>(mTabs.values())) {
            if (state.contents.isDestroyed() || state.tab.isDestroyed()) continue;
            GURL url = state.tab.getUrl();
            String host = ArgonBackgroundPlaybackStore.hostFromUrl(url);
            boolean foreground = isForeground(state.tab);
            if (foreground && (!state.wasForeground || !state.host.equals(host))) {
                mStore.policy.onForegroundVisit(host);
            }
            state.wasForeground = foreground;
            state.host = host;
            boolean allowed = mStore.policy.mayPlay(host, foreground, now, state.controllable);
            if (!allowed && state.playing) pause(state);
            boolean override = mStore.policy.isEnabled()
                    && mStore.policy.isSiteEnabled(host) && allowed;
            if (state.documentReady && (state.override == null || state.override != override)) {
                boolean needsScript = override || Boolean.TRUE.equals(state.override);
                state.override = override;
                if (needsScript && !mScript.isEmpty() && !host.isEmpty()) {
                    state.contents.evaluateJavaScript(
                            mScript.replace("ARGON_OVERRIDE_ACTIVE", Boolean.toString(override)), null);
                }
            }
            playing |= state.playing;
        }
        // No new wake lock, service, alarm permission or permanent background polling.
        if (playing && mStore.policy.isEnabled()) mHandler.postDelayed(mTick, 1000);
    }

    private void pause(State state) {
        if (state.contents.isDestroyed() || !state.controllable) return;
        state.playing = false;
        // UI suspend can be intercepted (and ignored) by the site's pause handler.
        // This public Chromium API suspends the media players directly and preserves position.
        state.contents.suspendAllMediaPlayers();
    }

    private String readScript() {
        StringBuilder script = new StringBuilder();
        try (BufferedReader reader = new BufferedReader(new InputStreamReader(
                mContext.getResources().openRawResource(R.raw.argon_background_playback),
                StandardCharsets.UTF_8))) {
            String line;
            while ((line = reader.readLine()) != null) script.append(line).append('\n');
        } catch (IOException ignored) {
            // Native deadline enforcement still works if the compatibility shim cannot load.
        }
        return script.toString();
    }

    private void destroy() {
        mDestroyed = true;
        mHandler.removeCallbacksAndMessages(null);
        mObserver.destroy();
        for (Tab tab : new ArrayList<>(mTabs.keySet())) untrack(tab);
        mContext.unregisterReceiver(mReceiver);
        mStore.preferences.unregisterOnSharedPreferenceChangeListener(mPreferences);
        ApplicationStatus.unregisterActivityStateListener(mActivityListener);
    }
}
