package org.chromium.chrome.browser.base;

import org.chromium.base.CommandLine;
import org.chromium.base.ContextUtils;
import org.chromium.chrome.browser.base.ArgonUserAgent.Preset;

public class ArgonUserAgentTest {
    public static void main(String[] args) {
        if (args[0].equals("save")) {
            ArgonUserAgent.applySavedOverride();
            ArgonUserAgent.save(Preset.CUSTOM,"SavedBrowser/2.0");
            assert ArgonUserAgent.hasPendingChange();
            assert ArgonUserAgent.getUserAgent(false).isEmpty();
            assert ArgonUserAgent.flushForRestart();
        } else if (args[0].equals("restored")) {
            ArgonUserAgent.applySavedOverride();
            assert !ArgonUserAgent.hasPendingChange();
            assert ArgonUserAgent.getUserAgent(false).equals("SavedBrowser/2.0");
            assert ArgonUserAgent.getUserAgent(true).equals("SavedBrowser/2.0");
        } else {
            ContextUtils.getAppSharedPreferences().edit()
                    .putString("argon_user_agent.preset",args[1])
                    .putString("argon_user_agent.custom","  ActiveBrowser/1.0  ").apply();
            Preset expected=Preset.valueOf(args[2]);
            ArgonUserAgent.applySavedOverride();
            assert ArgonUserAgent.getPreset()==expected;
            assert ArgonUserAgent.getActivePreset()==expected;
            assert !ArgonUserAgent.hasPendingChange();
            String mobile=ArgonUserAgent.getUserAgent(false);
            String desktop=ArgonUserAgent.getUserAgent(true);
            var switches=new java.util.HashMap<>(CommandLine.getInstance().values);
            if(expected==Preset.DEFAULT) {
                assert mobile.isEmpty() && desktop.isEmpty() && switches.isEmpty();
            } else {
                assert switches.get("argon-user-agent").equals(mobile);
                assert switches.get("argon-user-agent-desktop").equals(desktop);
                assert switches.containsKey("argon-user-agent-hide-client-hints");
                if(expected==Preset.CUSTOM) assert mobile.equals(desktop);
                else assert mobile.contains("Mobile") && !desktop.contains("Mobile");
            }
            for(Preset target:Preset.values()) {
                ArgonUserAgent.save(target,"NextBrowser/3.0");
                assert ArgonUserAgent.getPreset()==target;
                assert ArgonUserAgent.hasPendingChange()==(target!=expected || target==Preset.CUSTOM);
                // New tabs, workers and network contexts all keep the process snapshot.
                ArgonUserAgent.applySavedOverride();
                assert ArgonUserAgent.getUserAgent(false).equals(mobile);
                assert ArgonUserAgent.getUserAgent(true).equals(desktop);
                assert CommandLine.getInstance().values.equals(switches);
                ArgonUserAgent.discardPendingChange();
                assert !ArgonUserAgent.hasPendingChange();
            }
            for(String invalid:new String[]{""," ","Bad\r\nHeader","Bad\u0000","я","x".repeat(1025)}) {
                ArgonUserAgent.save(Preset.CUSTOM,invalid);
                assert !ArgonUserAgent.hasPendingChange();
            }
            assert ArgonUserAgent.isValidCustom("x".repeat(1024));
            ContextUtils.failCommit=true;
            assert !ArgonUserAgent.flushForRestart();
            ContextUtils.failCommit=false;
            assert ArgonUserAgent.flushForRestart();
            ContextUtils.getAppSharedPreferences().edit()
                    .putString("argon_user_agent.preset","CUSTOM")
                    .putString("argon_user_agent.custom","bad\nvalue").apply();
            assert ArgonUserAgent.getPreset()==Preset.DEFAULT;
        }
        System.out.println("PASS: startup snapshot, migration, pending selection and durable restart");
    }
}
