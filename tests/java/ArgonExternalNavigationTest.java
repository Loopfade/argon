package org.chromium.chrome.browser.externalnav;

import android.content.Context;
import android.content.Intent;
import androidx.preference.EditTextPreference;
import androidx.preference.ListPreference;
import androidx.preference.Preference;
import androidx.preference.PreferenceScreen;
import org.chromium.base.task.PostTask;
import org.chromium.chrome.browser.settings.ChromeBaseSettingsFragment;
import org.chromium.url.GURL;
import java.util.Set;

public class ArgonExternalNavigationTest {
    private static void check(boolean condition, String reason) {
        if (!condition) throw new AssertionError(reason);
    }
    private static Preference find(PreferenceScreen screen, String key) {
        for (Preference p : screen.rows) {
            if (key.equals(p.getKey())) return p;
            if (p instanceof PreferenceScreen group) {
                Preference found = find(group, key);
                if (found != null) return found;
            }
        }
        return null;
    }
    private static boolean blocked(String url) {
        return ArgonExternalNavigationPolicy.get().shouldBlock(new GURL(url), new Intent(url));
    }

    private static void policy() throws Exception {
        Context.values.clear();
        var policy=ArgonExternalNavigationPolicy.get();
        check(policy.getMode()==policy.ALLOW, "Fresh and existing installations keep Chromium's behavior");
        check(!blocked("tg://resolve?domain=example"), "Allow mode preserves app-only links");
        check(policy.addSite(" https://EXAMPLE.com./one?query=two#three "), "Pasted links are accepted");
        check(policy.getSites().equals(Set.of("example.com")), "Only the canonical full host is saved");
        check(!policy.addSite("example.com"), "Canonical duplicates are rejected");
        for (String invalid : new String[] {"", "co.uk", "github.io", "com", "localhost", "127.0.0.1",
                "*.example.com", "https://user:password@example.com", "https://exam ple.com", "mailto:user@example.com",
                "https://example.com\\@evil.test", "example..com"}) {
            check(ArgonExternalNavigationPolicy.normalizeSite(invalid)==null, "Invalid site: "+invalid);
        }
        check(policy.addSite("https://пример.рф/page"), "Internationalized domains are accepted");
        check(policy.getSites().contains("xn--e1afmkfd.xn--p1ai"), "IDNs share canonical storage");
        check(policy.addSite("m.video.test"), "A subdomain remains its own scope");
        policy.setMode(policy.ALLOW_LIST);
        check(!blocked("https://m.example.com/watch?v=1"), "All pages and subdomains are allowed");
        check(!blocked("http://EXAMPLE.COM./path"), "HTTP, case and DNS root dot use the same rule");
        check(blocked("https://notexample.com") && blocked("https://example.com.evil.test"), "DNS label boundaries cannot be bypassed");
        check(blocked("https://evil.test/?next=https://example.com"), "Query strings cannot grant permission");
        check(blocked("https://video.test") && blocked("https://other.video.test"), "A child rule cannot authorize its parent or sibling");
        check(!blocked("https://nested.m.video.test"), "Child rule covers its own descendants");
        check(blocked("tg://resolve?domain=example.com"), "A custom-scheme host is not a web destination");
        check(!policy.shouldBlock(new GURL("intent://example.com/#Intent;scheme=https;end"), new Intent("https://example.com")), "Web intent checks actual target data");
        check(policy.shouldBlock(new GURL("intent://example.com/#Intent;scheme=tg;end"), new Intent("tg://example.com")), "Intent spelling cannot authorize a custom scheme");
        check(policy.shouldBlock(new GURL("intent://example.com"), new Intent()), "Missing intent data grants nothing");
        policy.setMode(policy.BLOCK);
        check(blocked("https://example.com") && blocked("tel:123"), "Block mode overrides saved sites");
        check(!blocked("chrome://settings") && !blocked("file:///document.pdf"), "Internal pages and local documents remain usable");
        check(policy.getSites().contains("example.com"), "Mode changes preserve the list");
        policy.setMode(policy.ALLOW_LIST);
        policy.getSites().clear();
        check(!blocked("https://example.com"), "Callers cannot mutate SharedPreferences' set");
        policy.removeSite("example.com");
        check(blocked("https://example.com"), "Deleting a rule takes effect immediately in every tab");
        // Reconstruct the production store to check persistence rather than relying on an in-memory list.
        var singleton=ArgonExternalNavigationPolicy.class.getDeclaredField("sInstance");
        singleton.setAccessible(true); singleton.set(null, null);
        policy=ArgonExternalNavigationPolicy.get();
        check(policy.getMode()==policy.ALLOW_LIST && policy.getSites().contains("m.video.test"), "Mode and sites survive restart");
        Context.values.put("mode", 999);
        check(blocked("https://m.video.test"), "An invalid stored mode fails closed");
        Context.values.put("mode", "corrupt");
        check(blocked("https://m.video.test"), "A corrupt stored mode fails closed");
        Context.values.put("mode", policy.ALLOW_LIST); Context.values.put("sites", "corrupt");
        check(blocked("https://m.video.test"), "A corrupt site list grants no permission");
        Context.values.clear();
        for(int i=0;i<policy.MAX_SITES;i++) check(policy.addSite("site"+i+".test"), "Add up to the list limit");
        check(!policy.addSite("overflow.test"), "A second settings window cannot exceed the limit");
    }

    private static void navigation() {
        Context.values.clear();
        var policy=ArgonExternalNavigationPolicy.get();
        policy.setMode(policy.ALLOW_LIST);
        policy.addSite("allowed.test");
        var flow=new NavigationFlow();
        var empty=new GURL("");
        check(flow.navigate(new NavigationFlow.Params("https://allowed.test/a"), new Intent("https://allowed.test/a"), empty).action.equals("usual-browser-rules"), "Allowlisted URLs retain upstream checks");
        check(flow.launches==1, "Only normal rules may continue towards dispatch");
        check(flow.navigate(new NavigationFlow.Params("https://denied.test/b"), new Intent("https://denied.test/b"), empty).action.equals("web"), "A redirect to another site is evaluated independently");
        check(flow.launches==1, "A denied HTTP link cannot dispatch externally");
        var app=new NavigationFlow.Params("intent://denied.test");
        var fallback=new GURL("https://play.google.com/store/apps/details?id=test.app");
        check(flow.navigate(app,new Intent("tg://resolve"),fallback).action.equals("fallback:"+fallback.url), "Blocked intent fallbacks navigate inside the browser, including store URLs");
        check(flow.launches==1 && !app.started, "Fallback must not take the WebAPK or app-store launch branches");
        var webIntent=new NavigationFlow.Params("intent://denied.test");
        check(flow.navigate(webIntent,new Intent("https://denied.test/watch"),empty).action.equals("fallback:https://denied.test/watch"), "An intent's HTTP(S) data is usable as its web version without an explicit fallback");
        var allowedParams=new NavigationFlow.Params("https://allowed.test");
        check(flow.fallback(allowedParams,new GURL("https://denied.test"),true).action.equals("fallback:https://denied.test"), "An allowed initial destination cannot authorize an unlisted fallback WebAPK");
        check(flow.market(allowedParams,"missing.app",empty).action.startsWith("fallback:https://play.google.com/"), "A missing app cannot bypass the allow list by launching the store");
        check(flow.launches==1, "Fallback and store checks prevent all unlisted dispatches");
        policy.addSite("play.google.com");
        check(flow.market(allowedParams,"missing.app",empty).action.equals("usual-market-rules"), "An explicitly allowed store site retains Chromium rules");
        check(flow.fallback(allowedParams,new GURL("https://allowed.test/web"),true).action.equals("usual-fallback-rules"), "Allowed fallback URLs retain Chromium rules");
        flow.launches=1;
        var params=new NavigationFlow.Params("tg://resolve?domain=allowed.test");
        check(flow.navigate(params,new Intent("tg://resolve"),empty).action.equals("cancel"), "Blocked app-only links are consumed");
        check(params.started && !params.completed && flow.notices==1, "The current page stays while a notice explains the block");
        PostTask.drain();
        check(params.completed, "Async interception completes after the result is returned");
        params=new NavigationFlow.Params("mailto:person@example.com"); params.hasCallback=false;
        flow.navigate(params,new Intent("mailto:person@example.com"),empty); PostTask.drain();
        check(!params.completed && flow.launches==1, "A missing optional callback is safe");
        int notices=flow.notices;
        params=new NavigationFlow.Params("tg://resolve"); params.gesture=false;
        flow.navigate(params,new Intent("tg://resolve"),empty); PostTask.drain();
        params=new NavigationFlow.Params("tg://resolve"); params.background=true;
        flow.navigate(params,new Intent("tg://resolve"),empty); PostTask.drain();
        params=new NavigationFlow.Params("tg://resolve"); params.mainFrame=false;
        flow.navigate(params,new Intent("tg://resolve"),empty); PostTask.drain();
        check(flow.notices==notices && flow.launches==1, "Hidden frames and automatic redirects cannot spam UI notices");
        policy.setMode(policy.BLOCK);
        check(flow.navigate(new NavigationFlow.Params("https://allowed.test"),new Intent("https://allowed.test"),empty).action.equals("web"), "Block mode ignores the allow list");
        policy.setMode(policy.ALLOW);
        check(flow.navigate(new NavigationFlow.Params("tg://resolve"),new Intent("tg://resolve"),empty).action.equals("usual-browser-rules"), "Allow mode preserves ordinary browser handling");
    }

    private static void settings() {
        Context.values.clear();
        var policy=ArgonExternalNavigationPolicy.get();
        var page=new ArgonExternalNavigationSettings(); page.onCreatePreferences(null,null);
        var mode=(ListPreference)find(page.getPreferenceScreen(),"argon_external_mode");
        check(mode.entries.length==3 && mode.value.equals("0"), "Settings expose exactly three modes and the compatibility default");
        check(mode.change("1") && policy.getMode()==policy.ALLOW_LIST, "Selecting a mode immediately changes enforcement");
        check(find(page.getPreferenceScreen(),"argon_external_add").getFragment().equals(ArgonExternalNavigationSettings.SiteEditor.class.getName()), "Add opens the native site editor");
        var editor=new ArgonExternalNavigationSettings.SiteEditor(); editor.onCreatePreferences(null,null);
        var input=(EditTextPreference)find(editor.getPreferenceScreen(),"argon_external_site_input");
        var save=find(editor.getPreferenceScreen(),"argon_external_save");
        check(!save.isEnabled(), "An empty draft cannot be saved");
        var field=input.openDialog(); field.setText("https://EXAMPLE.com/some/path"); input.closeDialog(true);
        check(save.isEnabled() && policy.getSites().isEmpty(), "Entering a link previews a draft without granting access");
        var saved=new android.os.Bundle(); editor.onSaveInstanceState(saved);
        var restored=new ArgonExternalNavigationSettings.SiteEditor(); restored.onCreatePreferences(saved,null);
        check(((EditTextPreference)find(restored.getPreferenceScreen(),"argon_external_site_input")).getText().equals("https://EXAMPLE.com/some/path"), "Draft survives recreation");
        find(restored.getPreferenceScreen(),"argon_external_save").click();
        check(policy.getSites().equals(Set.of("example.com")) && ChromeBaseSettingsFragment.backPresses==1, "Explicit save persists the canonical whole site and closes the editor");
        editor=new ArgonExternalNavigationSettings.SiteEditor(); editor.onCreatePreferences(null,null);
        input=(EditTextPreference)find(editor.getPreferenceScreen(),"argon_external_site_input");
        field=input.openDialog(); field.setText("example.com"); input.closeDialog(true);
        check(!find(editor.getPreferenceScreen(),"argon_external_save").isEnabled(), "Duplicates cannot be saved");
        field=input.openDialog(); field.setText("other.test"); input.closeDialog(false);
        check(input.getText().equals("example.com") && !policy.getSites().contains("other.test"), "Cancel leaves the draft and list unchanged");
        page.onResume(); find(page.getPreferenceScreen(),"argon_external_site_example.com").click();
        check(policy.getSites().contains("example.com"), "Removing requires a concrete confirmation");
        android.app.AlertDialog.last.positive.accept(null,0);
        check(policy.getSites().isEmpty(), "Confirmed removal updates the screen and enforcement");
    }

    public static void main(String[] args) throws Exception {
        policy(); navigation(); settings();
        System.out.println("PASS: external navigation whole-site modes, storage, redirect/intent/fallback cancellation and native settings");
    }
}
