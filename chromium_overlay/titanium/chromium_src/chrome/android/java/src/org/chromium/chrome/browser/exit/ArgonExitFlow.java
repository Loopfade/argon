// Copyright 2026 Argon contributors
// SPDX-License-Identifier: GPL-2.0-only
package org.chromium.chrome.browser.exit;

import java.util.function.Consumer;

/** One asynchronous exit transaction. Backgrounding and playback never enter this flow. */
public final class ArgonExitFlow {
    public static final int ALL_TYPES = 63;
    public enum Phase { IDLE, SAVING, QUIESCING, CLEARING, COMMITTING, FAILED, DONE }
    /** Immutable snapshot; Chromium's Android DEX build does not support Java records. */
    public static final class Selection {
        private final int mTypes;
        private final boolean mCloseTabs;

        public Selection(int types, boolean closeTabs) {
            if (types < 0 || (types & ~ALL_TYPES) != 0) {
                throw new IllegalArgumentException("Unknown exit data category");
            }
            mTypes = types;
            mCloseTabs = closeTabs;
        }

        public int types() { return mTypes; }
        public boolean closeTabs() { return mCloseTabs; }
    }
    public interface Backend {
        void saveJournal(Selection selection, Consumer<Boolean> done);
        void quiesce(Selection selection, Consumer<Boolean> done);
        void clear(int types, Consumer<Integer> failedTypes);
        void completeJournal(Consumer<Boolean> done);
        void finished(boolean recovery);
        void failed(Phase phase, int failedTypes);
    }
    private final Backend mBackend;
    private Phase mPhase = Phase.IDLE;
    private Selection mSelection;
    private boolean mRecovery;
    private long mGeneration;

    public ArgonExitFlow(Backend backend) { mBackend = backend; }
    public Phase phase() { return mPhase; }
    public boolean isActive() { return mPhase != Phase.IDLE && mPhase != Phase.DONE; }

    /** Duplicate exit requests cannot change the snapshot or start another removal. */
    public void start(Selection selection, boolean recovery) {
        if (isActive()) return;
        mSelection = selection;
        mRecovery = recovery;
        run();
    }
    public void retry() {
        if (mPhase == Phase.FAILED) run();
    }
    /** Invalidate late native callbacks before an explicit close after an error/timeout. */
    public void abandon() { ++mGeneration; mPhase = Phase.DONE; }

    private void run() {
        long generation = ++mGeneration;
        mPhase = Phase.SAVING;
        mBackend.saveJournal(mSelection, saved -> {
            if (!current(generation, Phase.SAVING)) return;
            if (!saved) { fail(Phase.SAVING, mSelection.types()); return; }
            mPhase = Phase.QUIESCING;
            mBackend.quiesce(mSelection, stopped -> {
                if (!current(generation, Phase.QUIESCING)) return;
                if (!stopped) { fail(Phase.QUIESCING, mSelection.types()); return; }
                mPhase = Phase.CLEARING;
                mBackend.clear(mSelection.types(), failed -> {
                    if (!current(generation, Phase.CLEARING)) return;
                    if (failed != 0) { fail(Phase.CLEARING, failed); return; }
                    mPhase = Phase.COMMITTING;
                    mBackend.completeJournal(committed -> {
                        if (!current(generation, Phase.COMMITTING)) return;
                        if (!committed) { fail(Phase.COMMITTING, 0); return; }
                        mPhase = Phase.DONE;
                        mBackend.finished(mRecovery);
                    });
                });
            });
        });
    }
    private boolean current(long generation, Phase phase) {
        return generation == mGeneration && mPhase == phase;
    }
    private void fail(Phase phase, int types) {
        mPhase = Phase.FAILED;
        mBackend.failed(phase, types);
    }
}
