// Tests the production deferred-navigation branch inserted into TabImpl.
import java.util.ArrayList;
import java.util.List;

public final class ArgonExitTabTest {
    static final class Gate {
        static boolean blocked = true;
        static boolean isBlockingPages() { return blocked; }
    }
    record LoadUrlParams(String url) { String getUrl() { return url; } }
    record GURL(String url) {}
    record LoadUrlResult(int status, Object handle) {}
    static final class TabLoadStatus { static final int DEFAULT_PAGE_LOAD = 0; }
    static final class State {
        final List<String> history = new ArrayList<>(List.of("https://old.example/"));
        boolean canAppend = true;
        boolean destroyed;
        boolean appendPendingNavigation(Object profile, String title, LoadUrlParams params,
                                        boolean trackPending) {
            check(!destroyed && !trackPending, "invalid state append");
            if (!canAppend) return false;
            history.add(params.url());
            return true;
        }
        void destroy() { destroyed = true; }
    }
    static final class Tab {
        State mWebContentsState;
        LoadUrlParams mPendingLoadParams;
        GURL mUrl;
        int liveNavigations;
        Object getProfile() { return this; }
        String getTitle() { return "Tab"; }
        LoadUrlResult loadUrl(LoadUrlParams params) {
            // PRODUCTION_DEFERRED_LOAD
            ++liveNavigations;
            return new LoadUrlResult(1, null);
        }
        String recover() {
            check(mWebContentsState == null || mPendingLoadParams == null,
                    "serialized state conflicts with pending navigation");
            if (mPendingLoadParams != null) return mPendingLoadParams.url();
            var history = mWebContentsState.history;
            return history.get(history.size() - 1);
        }
    }
    static void check(boolean condition, String message) {
        if (!condition) throw new AssertionError(message);
    }
    public static void main(String[] args) {
        var first = new LoadUrlParams("https://first.example/");
        var latest = new LoadUrlParams("https://latest.example/");
        var fresh = new Tab();
        fresh.loadUrl(first);
        fresh.loadUrl(latest);
        check(fresh.liveNavigations == 0, "navigation started before cleanup");
        check(fresh.recover().equals(latest.url()), "fresh launch link lost");
        check(fresh.mUrl.url().equals(latest.url()), "pending visible URL lost");

        var frozen = new Tab();
        frozen.mWebContentsState = new State();
        frozen.loadUrl(latest);
        check(frozen.mPendingLoadParams == null, "pending params overlap frozen state");
        check(frozen.recover().equals(latest.url()), "VIEW link lost on frozen tab");
        check(frozen.mWebContentsState.history.get(0).equals("https://old.example/"),
                "existing navigation history lost");
        check(frozen.liveNavigations == 0, "frozen tab launched a renderer");

        var fallback = new Tab();
        var oldState = new State();
        oldState.canAppend = false;
        fallback.mWebContentsState = oldState;
        fallback.loadUrl(latest);
        check(oldState.destroyed, "failed state not released");
        check(fallback.recover().equals(latest.url()), "fallback link lost");

        Gate.blocked = false;
        var regular = new Tab();
        regular.loadUrl(latest);
        check(regular.liveNavigations == 1 && regular.mPendingLoadParams == null,
                "ordinary navigation changed");
        System.out.println("PASS: deferred VIEW navigation, frozen state and append failure");
    }
}
