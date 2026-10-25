// Copyright 2026 Argon contributors
// SPDX-License-Identifier: GPL-2.0-only
package org.chromium.chrome.browser.exit;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.Intent;
import android.os.Handler;
import android.os.Looper;

import org.chromium.base.ApplicationStatus;
import org.chromium.base.ContextUtils;
import org.chromium.base.ThreadUtils;
import org.chromium.chrome.R;
import org.chromium.chrome.browser.ChromeTabbedActivity;
import org.chromium.chrome.browser.TabPreferencesUtils;
import org.chromium.chrome.browser.app.ChromeActivity;
import org.chromium.chrome.browser.lifetime.ApplicationLifetime;
import org.chromium.chrome.browser.media.ArgonBackgroundPlaybackController;
import org.chromium.chrome.browser.profiles.Profile;
import org.chromium.chrome.browser.profiles.ProfileManager;
import org.chromium.content_public.browser.BrowserStartupController;

import java.lang.ref.WeakReference;
import java.util.ArrayList;
import java.util.function.Consumer;

/** Process-scoped explicit exit. Lifecycle callbacks only attach the progress/error UI. */
public final class ArgonExitCoordinator {
    private static final Handler MAIN = new Handler(Looper.getMainLooper());
    private static final long TIMEOUT_MS = 30_000;
    private static boolean sClosing;
    private static boolean sInitialized;
    private static ArgonExitCoordinator sInstance;
    private static WeakReference<Activity> sActivity = new WeakReference<>(null);
    private final Profile mProfile;
    private final String mProfileKey;
    private final ArgonExitFlow mFlow;
    private final boolean mRecovery;
    private boolean mCloseWhenRecovered;
    private boolean mTimedOut;
    private ArgonExitFlow.Phase mFailure;
    private AlertDialog mDialog;
    private boolean mCloseTabs;
    private WeakReference<Activity> mDialogOwner = new WeakReference<>(null);
    private int mTimeoutGeneration;

    public static boolean isBlockingPages() {
        return sClosing || ArgonExitStore.pending();
    }
    public static boolean isBackgroundWorkBlocked() {
        return isBlockingPages() || ArgonExitStore.backgroundBlocked();
    }
    /** Service restarts must not override a user's explicit exit. */
    public static boolean shouldAllowRestart() { return !isBackgroundWorkBlocked(); }
    public static boolean isClosing() { return sClosing; }
    public static boolean shouldDiscardRestoredTabs() {
        if (!ArgonExitStore.pending()) return false;
        try { return ArgonExitStore.snapshot().closeTabs(); }
        catch (RuntimeException invalidJournal) { return true; }
    }
    public static boolean shouldPreserveTabs() { return sInstance == null || !sInstance.mCloseTabs; }
    public static void installRestartGuard() {
        if (sInitialized) return;
        sInitialized = true;
        ApplicationLifetime.setRestartAllowedSupplier(ArgonExitCoordinator::shouldAllowRestart);
        // Observe the profile module from the browser layer, avoiding a reverse Java dependency.
        ProfileManager.addObserver(new ProfileManager.Observer() {
            @Override
            public void onProfileAdded(Profile profile) { onProfileReady(profile); }
            @Override
            public void onProfileDestroyed(Profile profile) {
                if (sInstance != null && sInstance.mProfile == profile) {
                    sInstance.mFlow.abandon();
                    sInstance.mFailure = ArgonExitFlow.Phase.QUIESCING;
                    sInstance.showStatus();
                }
            }
        });
        if (ProfileManager.isInitialized()) onProfileReady(ProfileManager.getLastUsedRegularProfile());
    }

    public static void requestExit() {
        ThreadUtils.assertOnUiThread();
        if (sInstance != null) {
            sInstance.mCloseWhenRecovered = true;
            sInstance.showStatus();
            return;
        }
        if (!ProfileManager.isInitialized()) return;
        if (ArgonExitStore.pending()) {
            onProfileReady(ProfileManager.getLastUsedRegularProfile());
            if (sInstance != null) sInstance.mCloseWhenRecovered = true;
            return;
        }
        Profile profile = ProfileManager.getLastUsedRegularProfile();
        sClosing = true;
        sInstance = new ArgonExitCoordinator(profile, false);
        sInstance.start(new ArgonExitFlow.Selection(
                ArgonExitStore.selection(sInstance.mProfileKey),
                TabPreferencesUtils.shouldCloseTabsOnExit()));
    }

    /** Called once a profile is available; polling ends when native startup completes. */
    public static void onProfileReady(Profile profile) {
        if (profile.isOffTheRecord() || !ArgonExitStore.pending() || sInstance != null) return;
        if (sActivity.get() == null) return;
        MAIN.post(() -> recoverWhenReady(profile, android.os.SystemClock.elapsedRealtime()));
    }
    private static void recoverWhenReady(Profile profile, long started) {
        if (!ArgonExitStore.pending() || sInstance != null) return;
        if (sActivity.get() == null) return;
        if (!BrowserStartupController.getInstance().isFullBrowserStarted()) {
            if (android.os.SystemClock.elapsedRealtime() - started < TIMEOUT_MS) {
                MAIN.postDelayed(() -> recoverWhenReady(profile, started), 100);
            } else {
                sClosing = true;
                sInstance = new ArgonExitCoordinator(profile, true);
                sInstance.mTimedOut = true;
                sInstance.showStatus();
            }
            return;
        }
        sClosing = true;
        sInstance = new ArgonExitCoordinator(profile, true);
        try {
            if (!sInstance.mProfileKey.equals(ArgonExitStore.pendingProfile())) {
                throw new IllegalStateException("Pending cleanup belongs to another profile");
            }
            sInstance.start(ArgonExitStore.snapshot());
        } catch (RuntimeException error) {
            sInstance.mFailure = ArgonExitFlow.Phase.SAVING;
            sInstance.showStatus();
        }
    }

    /** A visible Activity is needed for UI, never for owning deletion or its callbacks. */
    public static void onActivityReady(Activity activity) {
        sActivity = new WeakReference<>(activity);
        if (sInstance != null) { sInstance.showStatus(); return; }
        if (!ArgonExitStore.pending()) { ArgonExitStore.manualOpen(); return; }
        if (ProfileManager.isInitialized()) onProfileReady(ProfileManager.getLastUsedRegularProfile());
    }
    public static void onActivityHidden(Activity activity) {
        if (sActivity.get() == activity) sActivity = new WeakReference<>(null);
        if (sInstance != null && sInstance.mDialogOwner.get() == activity) sInstance.dismiss();
    }

    private ArgonExitCoordinator(Profile profile, boolean recovery) {
        mProfile = profile.getOriginalProfile();
        mProfileKey = ArgonExitBridgeJni.get().profileKey(mProfile);
        mRecovery = recovery;
        mFlow = new ArgonExitFlow(new ArgonExitFlow.Backend() {
            @Override
            public void saveJournal(ArgonExitFlow.Selection selection, Consumer<Boolean> done) {
                ArgonExitStore.begin(mProfileKey, selection, done);
            }
            @Override
            public void quiesce(ArgonExitFlow.Selection selection, Consumer<Boolean> done) {
                ArgonBackgroundPlaybackController.stopForExit();
                ArgonExitBridgeJni.get().prepare(mProfile, selection.closeTabs(), mRecovery, stopped -> {
                    // Explicit stop clears START_STICKY's started state; native downloads are paused.
                    stopDownloadService();
                    done.accept(stopped);
                });
            }
            @Override
            public void clear(int types, Consumer<Integer> done) {
                ArgonExitBridgeJni.get().clear(mProfile, types, done::accept);
            }
            @Override
            public void completeJournal(Consumer<Boolean> done) {
                try {
                    // Save frozen navigation state (or the empty models) before process termination.
                    for (Activity activity : new ArrayList<>(ApplicationStatus.getRunningActivities())) {
                        if (activity instanceof ChromeTabbedActivity) {
                            ((ChromeTabbedActivity) activity).saveState();
                        }
                    }
                } catch (RuntimeException error) { done.accept(false); return; }
                ArgonExitStore.complete(mRecovery && !mCloseWhenRecovered, done);
            }
            @Override
            public void finished(boolean recovery) {
                ++mTimeoutGeneration;
                dismiss();
                if (recovery && !mCloseWhenRecovered) {
                    sClosing = false;
                    sInstance = null;
                    ArgonExitBridgeJni.get().resume(mProfile);
                    ArgonBackgroundPlaybackController.resumeAfterRecovery();
                    for (Activity activity : new ArrayList<>(ApplicationStatus.getRunningActivities())) {
                        if (activity instanceof ChromeActivity) {
                            var tab = ((ChromeActivity) activity).getActivityTab();
                            if (tab != null) tab.loadIfNeeded(false);
                        }
                    }
                } else {
                    ApplicationLifetime.terminate(false);
                }
            }
            @Override
            public void failed(ArgonExitFlow.Phase phase, int failedTypes) {
                ++mTimeoutGeneration;
                mFailure = phase;
                showStatus();
            }
        });
    }
    private void start(ArgonExitFlow.Selection selection) {
        mCloseTabs = selection.closeTabs();
        showStatus();
        mFlow.start(selection, mRecovery);
        armTimeout();
    }
    private void armTimeout() {
        int generation = ++mTimeoutGeneration;
        MAIN.postDelayed(() -> {
            if (generation == mTimeoutGeneration && sInstance == this
                    && mFlow.isActive() && mFlow.phase() != ArgonExitFlow.Phase.FAILED) {
                mTimedOut = true;
                showStatus();
            }
        }, TIMEOUT_MS);
    }
    private void dismiss() {
        if (mDialog != null) {
            try { mDialog.dismiss(); } catch (IllegalArgumentException detachedWindow) {}
            mDialog = null;
            mDialogOwner = new WeakReference<>(null);
        }
    }
    private static void stopDownloadService() {
        var context = ContextUtils.getApplicationContext();
        context.stopService(new Intent().setClassName(context.getPackageName(),
                "org.chromium.chrome.browser.download.DownloadForegroundService"));
    }
    private void showStatus() {
        Activity activity = sActivity.get();
        if (activity == null || activity.isFinishing() || activity.isDestroyed()) return;
        dismiss();
        var dialog = new AlertDialog.Builder(activity)
                .setTitle(R.string.argon_exit_title).setCancelable(false);
        if (mFailure != null || mTimedOut) {
            dialog.setMessage(mFailure == ArgonExitFlow.Phase.SAVING
                    ? R.string.argon_exit_save_failed : R.string.argon_exit_failed);
            if (mFailure != null && mFlow.phase() == ArgonExitFlow.Phase.FAILED) {
                dialog.setPositiveButton(R.string.argon_exit_retry, (ignored, which) -> {
                    mFailure = null;
                    mTimedOut = false;
                    showStatus();
                    mFlow.retry();
                    armTimeout();
                });
            }
            dialog.setNegativeButton(R.string.argon_exit_close_pending, (ignored, which) -> {
                mFlow.abandon();
                // A timeout may precede native's callback: clear START_STICKY before killing us.
                stopDownloadService();
                ApplicationLifetime.terminate(false);
            });
        } else {
            dialog.setMessage(mRecovery ? R.string.argon_exit_recovering : R.string.argon_exit_progress);
        }
        mDialogOwner = new WeakReference<>(activity);
        mDialog = dialog.show();
    }
}
