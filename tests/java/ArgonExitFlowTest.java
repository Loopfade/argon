package org.chromium.chrome.browser.exit;

import java.util.ArrayList;
import java.util.List;
import java.util.function.Consumer;

/** Adversarial completion order, durable-write failures and interrupted startup. */
public final class ArgonExitFlowTest {
    private static void check(boolean value, String message) {
        if (!value) throw new AssertionError(message);
    }
    private static final class Backend implements ArgonExitFlow.Backend {
        final List<String> calls = new ArrayList<>();
        ArgonExitFlow.Selection saved;
        Consumer<Boolean> savedCallback, stoppedCallback, committedCallback;
        Consumer<Integer> clearCallback;
        int failures;
        @Override public void saveJournal(ArgonExitFlow.Selection selection, Consumer<Boolean> done) {
            calls.add("journal"); saved = selection; savedCallback = done;
        }
        @Override public void quiesce(ArgonExitFlow.Selection selection, Consumer<Boolean> done) {
            calls.add("stop"); stoppedCallback = done;
        }
        @Override public void clear(int types, Consumer<Integer> done) {
            calls.add("clear:" + types); clearCallback = done;
        }
        @Override public void completeJournal(Consumer<Boolean> done) {
            calls.add("commit"); committedCallback = done;
        }
        @Override public void finished(boolean recovery) { calls.add(recovery ? "resume" : "exit"); }
        @Override public void failed(ArgonExitFlow.Phase phase, int types) {
            calls.add("failed:" + phase + ":" + types); ++failures;
        }
    }
    private static void success(int types, boolean recovery) {
        Backend b = new Backend(); ArgonExitFlow flow = new ArgonExitFlow(b);
        flow.start(new ArgonExitFlow.Selection(types, true), recovery);
        check(b.calls.equals(List.of("journal")), "Stopping pages must wait for a durable journal");
        flow.start(new ArgonExitFlow.Selection(63, false), !recovery);
        check(b.saved.types() == types && b.saved.closeTabs(), "Repeated exit changed the snapshot");
        Consumer<Boolean> oldSave = b.savedCallback;
        b.savedCallback.accept(true);
        oldSave.accept(true);
        check(b.calls.equals(List.of("journal", "stop")), "Duplicate callback started more work");
        b.stoppedCallback.accept(true);
        check(!b.calls.contains("exit"), "Process killed before deletion completed");
        b.clearCallback.accept(0);
        check(b.calls.get(b.calls.size()-1).equals("commit"), "Journal completion must be durable");
        b.committedCallback.accept(true);
        check(b.calls.get(b.calls.size()-1).equals(recovery ? "resume" : "exit"), "Wrong final action");
        check(flow.phase() == ArgonExitFlow.Phase.DONE, "Success did not complete");
    }
    private static void failuresAndRetry() {
        for (int stage = 0; stage < 4; ++stage) {
            Backend b = new Backend(); ArgonExitFlow flow = new ArgonExitFlow(b);
            flow.start(new ArgonExitFlow.Selection(3, false), false);
            Consumer<Boolean> stale = b.savedCallback;
            b.savedCallback.accept(stage != 0);
            if (stage > 0) b.stoppedCallback.accept(stage != 1);
            if (stage > 1) b.clearCallback.accept(stage == 2 ? 2 : 0);
            if (stage > 2) b.committedCallback.accept(false);
            check(flow.phase() == ArgonExitFlow.Phase.FAILED && b.failures == 1,
                    "An unconfirmed stage was treated as success");
            check(!b.calls.contains("exit") && !b.calls.contains("resume"), "Failure released the gate");
            flow.retry();
            stale.accept(true);
            check(flow.phase() == ArgonExitFlow.Phase.SAVING, "An old operation completed a retry");
            b.savedCallback.accept(true); b.stoppedCallback.accept(true);
            b.clearCallback.accept(0); b.committedCallback.accept(true);
            check(flow.phase() == ArgonExitFlow.Phase.DONE, "Retry did not finish");
        }
    }
    private static void closeWhilePending() {
        Backend b = new Backend(); ArgonExitFlow flow = new ArgonExitFlow(b);
        flow.start(new ArgonExitFlow.Selection(63, true), false);
        b.savedCallback.accept(true); b.stoppedCallback.accept(true);
        Consumer<Integer> nativeCallback = b.clearCallback;
        flow.abandon(); nativeCallback.accept(0);
        check(!b.calls.contains("commit") && !b.calls.contains("exit"),
                "A late callback cleared the interrupted journal after an explicit close");
    }
    public static void main(String[] args) {
        for (int mask = 0; mask <= 63; ++mask) { success(mask, false); success(mask, true); }
        failuresAndRetry(); closeWhilePending();
        for (int invalid : new int[] {-1, 64, Integer.MAX_VALUE}) {
            try { new ArgonExitFlow.Selection(invalid, false); throw new AssertionError("Unknown category accepted"); }
            catch (IllegalArgumentException expected) {}
        }
        System.out.println("PASS: exit ordering, all category selections, recovery, failures, retries and stale callbacks");
    }
}
