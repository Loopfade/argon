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
import android.media.AudioAttributes;
import android.media.AudioDeviceCallback;
import android.media.AudioDeviceInfo;
import android.media.AudioManager;
import android.os.Build;
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
import org.chromium.url.GURL;

import java.io.BufferedReader;
import java.io.IOException;
import java.io.InputStreamReader;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.Map;
import java.util.TreeSet;

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
    private final @Nullable AudioManager mAudio = mContext.getSystemService(AudioManager.class);
    private final String mScript;
    private String mRoute = "";
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
            if (AudioManager.ACTION_AUDIO_BECOMING_NOISY.equals(intent.getAction())) {
                if (mStore.policy.isEnabled() && mStore.pauseOnHeadphones()) pauseManagedSites();
            }
            refresh();
            // Screen-on arrives before keyguard/window focus has necessarily settled.
            mHandler.postDelayed(mTick, 200);
        }
    };
    private final AudioDeviceCallback mAudioDevices = new AudioDeviceCallback() {
        @Override
        public void onAudioDevicesAdded(AudioDeviceInfo[] devices) {
            mHandler.postDelayed(mTick, 100);
        }

        @Override
        public void onAudioDevicesRemoved(AudioDeviceInfo[] devices) {
            mHandler.postDelayed(mTick, 100);
        }
    };

    private static final class State {
        final Tab tab;
        final WebContents contents;
        final MediaSession session;
        @Nullable MediaSessionObserver observer;
        boolean controllable;
        boolean playing;
        boolean wasForeground;
        boolean documentReady = true;
        @Nullable Boolean override;
        String host = "";

        State(Tab tab, WebContents contents) {
            this.tab = tab;
            this.contents = contents;
            session = MediaSession.fromWebContents(contents);
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
        filter.addAction(AudioManager.ACTION_AUDIO_BECOMING_NOISY);
        ContextUtils.registerNonExportedBroadcastReceiver(mContext, mReceiver, filter);
        mStore.preferences.registerOnSharedPreferenceChangeListener(mPreferences);
        ApplicationStatus.registerStateListenerForActivity(mActivityListener, activity);
        if (mAudio != null) mAudio.registerAudioDeviceCallback(mAudioDevices, mHandler);
    }

    private void track(Tab tab) {
        if (tab.isOffTheRecord() || tab.isDestroyed() || mTabs.containsKey(tab)) return;
        WebContents contents = tab.getWebContents();
        if (contents == null || contents.isDestroyed()) return;
        State state = new State(tab, contents);
        mTabs.put(tab, state);
        state.observer = new MediaSessionObserver(state.session) {
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
            }
        };
        refresh();
    }

    private void untrack(Tab tab) {
        State state = mTabs.remove(tab);
        if (state != null && state.observer != null) state.observer.stopObserving();
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
        boolean watchRoute = false;
        if (mStore.policy.isEnabled() && mStore.pauseOnHeadphones()) {
            for (State state : mTabs.values()) {
                watchRoute |= state.playing && mStore.policy.isSiteEnabled(state.host);
            }
        }
        if (watchRoute) {
            String route = audioRoute();
            if (!route.isEmpty() && !mRoute.isEmpty() && !route.equals(mRoute)) pauseManagedSites();
            mRoute = route;
        } else {
            mRoute = "";
        }
        for (State state : new ArrayList<>(mTabs.values())) {
            if (state.contents.isDestroyed() || state.tab.isDestroyed()) continue;
            GURL url = state.tab.getUrl();
            String host = "https".equals(url.getScheme()) ? url.getHost() : "";
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

    private void pauseManagedSites() {
        for (State state : new ArrayList<>(mTabs.values())) {
            if (state.playing && mStore.policy.isSiteEnabled(state.host)) pause(state);
        }
        // Never automatically resume after a route change.
    }

    private String audioRoute() {
        if (mAudio == null) return "";
        TreeSet<Integer> devices = new TreeSet<>();
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            AudioAttributes attributes = new AudioAttributes.Builder()
                    .setUsage(AudioAttributes.USAGE_MEDIA).build();
            for (AudioDeviceInfo device : mAudio.getAudioDevicesForAttributes(attributes)) {
                devices.add(device.getId());
            }
        } else {
            // Older Android exposes connected outputs rather than the selected media route.
            for (AudioDeviceInfo device : mAudio.getDevices(AudioManager.GET_DEVICES_OUTPUTS)) {
                int type = device.getType();
                if (type == AudioDeviceInfo.TYPE_WIRED_HEADSET
                        || type == AudioDeviceInfo.TYPE_WIRED_HEADPHONES
                        || type == AudioDeviceInfo.TYPE_BLUETOOTH_A2DP
                        || type == AudioDeviceInfo.TYPE_USB_HEADSET) devices.add(device.getId());
            }
            devices.add(-1); // The empty-headphone/speaker route must also have a signature.
        }
        return devices.toString();
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
        if (mAudio != null) mAudio.unregisterAudioDeviceCallback(mAudioDevices);
    }
}
