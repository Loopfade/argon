// Copyright 2026 Argon contributors
// SPDX-License-Identifier: GPL-2.0-only
package org.chromium.chrome.browser.exit;

import android.content.Context;

import org.chromium.components.background_task_scheduler.BackgroundTask;
import org.chromium.components.background_task_scheduler.TaskParameters;

/** Completes a scheduled wake-up without loading native or resuming downloads after Exit. */
public final class ArgonExitBackgroundTask implements BackgroundTask {
    @Override
    public boolean onStartTask(Context context, TaskParameters params, TaskFinishedCallback callback) {
        return false;
    }
    @Override
    public boolean onStopTask(Context context, TaskParameters params) { return false; }
}
