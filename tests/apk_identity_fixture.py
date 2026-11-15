"""aapt2 XmlPrinter's URI attributes; identifiers follow pinned Chromium."""
def manifest_dump(package="app.titaniumru.browser.test", version_code=1000000001, *, legacy=False):
    text = f'''N: android=http://schemas.android.com/apk/res/android (line=1)
  E: manifest (line=1)
    A: package="{package}" (Raw: "{package}")
    A: android:versionCode(0x0101021b)=(type 0x10)0x{version_code:x}
    E: permission (line=2)
      A: android:name(0x01010003)="{package}.permission.CHILD_SERVICE" (Raw: "{package}.permission.CHILD_SERVICE")
      A: android:protectionLevel(0x01010009)=(type 0x11)0x2
    E: permission (line=3)
      A: android:name(0x01010003)="{package}.permission.C2D_MESSAGE" (Raw: "{package}.permission.C2D_MESSAGE")
    E: uses-permission (line=4)
      A: android:name(0x01010003)="{package}.permission.C2D_MESSAGE" (Raw: "{package}.permission.C2D_MESSAGE")
    E: application (line=5)
      E: provider (line=6)
        A: android:name(0x01010003)="org.chromium.chrome.browser.util.ChromeFileProvider" (Raw: "org.chromium.chrome.browser.util.ChromeFileProvider")
        A: android:authorities(0x01010018)="{package}.FileProvider" (Raw: "{package}.FileProvider")
        A: android:exported(0x01010010)=(type 0x12)0x0
      E: provider (line=7)
        A: android:name(0x01010003)="org.chromium.chrome.browser.provider.ChromeBrowserProvider"
        A: android:authorities(0x01010018)="{package}.ChromeBrowserProvider;{package}.browser;{package}"
      E: activity (line=8)
        A: android:name(0x01010003)="org.chromium.chrome.browser.ChromeTabbedActivity"
        A: android:taskAffinity(0x01010012)="{package}.ChromeTabbedActivity2"
        E: intent-filter (line=9)
          E: data (line=10)
            A: android:scheme(0x01010027)="http"
          E: data (line=11)
            A: android:scheme(0x01010027)="https"
      E: service (line=12)
        A: android:name(0x01010003)="org.chromium.chrome.browser.services.gcm.ChromeGcmListenerService"
        A: android:process(0x01010011)=":sandboxed_process0"
        E: intent-filter (line=13)
          E: action (line=14)
            A: android:name(0x01010003)="com.google.firebase.MESSAGING_EVENT"
          E: category (line=15)
            A: android:name(0x01010003)="{package}"
      E: activity (line=919)
        A: android:name(0x01010003)="androidx.core.pip.contentpip.ContentPipInternalActivity"
        A: android:exported(0x01010010)=(type 0x12)0x0
        A: android:taskAffinity(0x01010012)="{package}.androidx.core.pip.contentpip.isolated"
        A: android:excludeFromRecents(0x01010017)=(type 0x12)0xffffffff
        A: android:supportsPictureInPicture(0x010104f7)=(type 0x12)0xffffffff
'''
    if legacy:
        return text
    # tools/aapt2/Debug.cpp prints namespace_uri + ':' + attribute name;
    # ResourceValues.cpp prints TYPE_INT_DEC as a decimal integer.
    return (text.replace("android:", "http://schemas.android.com/apk/res/android:")
            .replace(f"(type 0x10)0x{version_code:x}", str(version_code))
            .replace("(type 0x11)0x2", "0x00000002")
            .replace("(type 0x12)0x0", "false"))
