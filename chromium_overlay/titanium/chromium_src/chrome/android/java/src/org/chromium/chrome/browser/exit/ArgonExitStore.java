// Copyright 2026 Argon contributors
// SPDX-License-Identifier: GPL-2.0-only
package org.chromium.chrome.browser.exit;

import android.content.Context;
import android.content.SharedPreferences;
import android.os.Handler;
import android.os.Looper;

import org.chromium.base.ContextUtils;

import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.function.Consumer;

/** A durable journal separate from cookies, site storage and the homepage dump. */
final class ArgonExitStore {
    private static final ExecutorService WRITER = Executors.newSingleThreadExecutor();
    private static SharedPreferences prefs() {
        return ContextUtils.getApplicationContext().getSharedPreferences(
                "argon_exit", Context.MODE_PRIVATE);
    }
    static int selection(String profile) {
        int value = prefs().getInt("types:" + profile, 0);
        return value >= 0 && (value & ~ArgonExitFlow.ALL_TYPES) == 0 ? value : 0;
    }
    static void select(String profile, int types) {
        prefs().edit().putInt("types:" + profile, types & ArgonExitFlow.ALL_TYPES).apply();
    }
    static boolean pending() { return prefs().getBoolean("pending", false); }
    static boolean backgroundBlocked() { return prefs().getBoolean("explicit_exit", false); }
    static String pendingProfile() { return prefs().getString("profile", ""); }
    static ArgonExitFlow.Selection snapshot() {
        if (prefs().getInt("journal_version", 0) != 1) {
            throw new IllegalStateException("Unknown exit journal version");
        }
        return new ArgonExitFlow.Selection(prefs().getInt("snapshot_types", 0),
                prefs().getBoolean("snapshot_close_tabs", false));
    }
    static void begin(String profile, ArgonExitFlow.Selection snapshot, Consumer<Boolean> done) {
        write(() -> prefs().edit().putBoolean("pending", true)
                .putInt("journal_version", 1)
                .putBoolean("explicit_exit", true).putString("profile", profile)
                .putInt("snapshot_types", snapshot.types())
                .putBoolean("snapshot_close_tabs", snapshot.closeTabs()).commit(), done);
    }
    static void complete(boolean recovery, Consumer<Boolean> done) {
        write(() -> prefs().edit().putBoolean("pending", false)
                .putBoolean("explicit_exit", !recovery).remove("profile")
                .remove("snapshot_types").remove("snapshot_close_tabs").commit(), done);
    }
    static void manualOpen() {
        if (!pending()) prefs().edit().putBoolean("explicit_exit", false).apply();
    }
    private interface Write { boolean run(); }
    private static void write(Write action, Consumer<Boolean> done) {
        Handler main = new Handler(Looper.getMainLooper());
        WRITER.execute(() -> {
            boolean success;
            try { success = action.run(); } catch (RuntimeException error) { success = false; }
            final boolean result = success;
            main.post(() -> done.accept(result));
        });
    }
}
