package org.chromium.chrome.browser.privacy.settings;

import androidx.appcompat.app.AlertDialog;
import androidx.preference.PreferenceFragmentCompat;
import android.widget.EditText;
import org.chromium.base.ContextUtils;
import org.chromium.base.CommandLine;
import org.chromium.chrome.R;
import org.chromium.chrome.browser.base.ArgonUserAgent;
import org.chromium.chrome.browser.base.ArgonUserAgent.Preset;
import org.chromium.chrome.browser.base.UserAgentStartup;
import org.chromium.chrome.browser.lifetime.ApplicationLifetime;

public class ArgonUserAgentSettingsTest {
    public static void main(String[] args) {
        Preset active=Preset.valueOf(args[0]);
        ArgonUserAgent.save(active,"ActiveBrowser/1.0");
        UserAgentStartup.initialize();
        var switches=new java.util.HashMap<>(CommandLine.getInstance().values);
        var fragment=new PreferenceFragmentCompat();
        ArgonUserAgentPreference.bind(fragment);
        var selection=fragment.findPreference("argon_user_agent");
        var restart=fragment.findPreference("argon_user_agent_restart");
        assert !restart.visible && !ArgonUserAgent.hasPendingChange();

        selection.click();
        assert AlertDialog.latest.choices.length==4 && AlertDialog.latest.checked==active.ordinal();
        AlertDialog.latest.press(AlertDialog.BUTTON_NEGATIVE);
        assert !ArgonUserAgent.hasPendingChange();
        selection.click();
        AlertDialog.latest.select(active.ordinal());
        if(active==Preset.CUSTOM) AlertDialog.latest.press(AlertDialog.BUTTON_POSITIVE);
        assert !ArgonUserAgent.hasPendingChange() && ApplicationLifetime.restarts==0;

        selection.click();
        AlertDialog.latest.select(Preset.FIREFOX.ordinal());
        assert restart.visible && ArgonUserAgent.hasPendingChange();
        assert ArgonUserAgent.getActivePreset()==active;
        assert selection.summary.toString().contains("Firefox");
        assert AlertDialog.latest.message==R.string.argon_user_agent_restart_message;
        AlertDialog.latest.press(AlertDialog.BUTTON_NEGATIVE); // Later.
        assert ApplicationLifetime.restarts==0 && restart.visible;
        assert CommandLine.getInstance().values.equals(switches);
        var reopened=new PreferenceFragmentCompat();
        ArgonUserAgentPreference.bind(reopened);
        assert reopened.findPreference("argon_user_agent_restart").visible;
        restart.click();
        AlertDialog.latest.press(AlertDialog.BUTTON_NEUTRAL); // Undo.
        assert !restart.visible && !ArgonUserAgent.hasPendingChange();
        reopened.getLifecycle().resume();
        assert !reopened.findPreference("argon_user_agent_restart").visible;

        selection.click();
        AlertDialog.latest.select(Preset.CUSTOM.ordinal());
        EditText.latest.setText("DiscardedBrowser/1.0");
        AlertDialog.latest.press(AlertDialog.BUTTON_NEGATIVE);
        assert !ArgonUserAgent.hasPendingChange();
        selection.click();
        AlertDialog.latest.select(Preset.CUSTOM.ordinal());
        var editor=AlertDialog.latest;
        for(String invalid:new String[]{""," ","Bad\r\nHeader","я","x".repeat(1025)}) {
            EditText.latest.setText(invalid);
            editor.press(AlertDialog.BUTTON_POSITIVE);
            assert editor.shown && EditText.latest.error!=null;
            assert !ArgonUserAgent.hasPendingChange();
        }
        EditText.latest.setText("  NextBrowser/2.0  ");
        editor.press(AlertDialog.BUTTON_POSITIVE);
        assert !editor.shown && ArgonUserAgent.getCustom().equals("NextBrowser/2.0");
        assert ArgonUserAgent.hasPendingChange() && restart.visible;
        if(active==Preset.CUSTOM) {
            assert selection.summary.equals(selection.getContext().getString(R.string.argon_user_agent_custom_pending));
        }
        assert ArgonUserAgent.getActivePreset()==active;
        assert CommandLine.getInstance().values.equals(switches);
        AlertDialog.latest.press(AlertDialog.BUTTON_NEGATIVE);
        selection.click();
        AlertDialog.latest.select(Preset.CUSTOM.ordinal());
        assert EditText.latest.getText().toString().equals("NextBrowser/2.0");
        AlertDialog.latest.press(AlertDialog.BUTTON_NEGATIVE);

        ContextUtils.failCommit=true;
        restart.click();
        AlertDialog.latest.press(AlertDialog.BUTTON_POSITIVE);
        assert android.widget.Toast.shown==1 && ApplicationLifetime.restarts==0;
        assert ArgonUserAgent.hasPendingChange();
        ContextUtils.failCommit=false;
        restart.click();
        AlertDialog.latest.press(AlertDialog.BUTTON_POSITIVE);
        assert ApplicationLifetime.restarts==1;
        assert CommandLine.getInstance().values.equals(switches);
        System.out.println("PASS: settings UI and restart lifecycle");
    }
}
