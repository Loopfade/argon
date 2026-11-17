"""Execute Titanium's Android API guard on Vanadium's production location bridge."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = "org/chromium/device/geolocation/"
TARGET = ("titanium/chromium_src/services/downstream/device/geolocation/android/java/src/"
          + PACKAGE + "CoarseLocationRequests.java")


@unittest.skipUnless(shutil.which("java"), "JDK required for the location API regression")
class CoarseLocationTests(unittest.TestCase):
    def test_legacy_android_is_not_sent_to_api_31_and_supported_requests_still_work(self):
        patches = list((ROOT / "vanadium/patches").glob("*coarse-location*.patch"))
        self.assertEqual(len(patches), 1)
        marker = "+++ b/" + TARGET.replace("titanium/", "vanadium/", 1) + "\n"
        source = patches[0].read_text().split(marker, 1)[1].split("diff --git ", 1)[0]
        source = "\n".join(line[1:] for line in source.splitlines()
                           if line.startswith("+") and not line.startswith("+++")) + "\n"
        source = source.replace("vanadium", "titanium")
        commands = [line for line in (ROOT / "patch.sh").read_text().splitlines()
                    if line.startswith("sed ") and TARGET in line]
        self.assertEqual(len(commands), 1)
        adapters = {
            "android/os/Build.java": '''package android.os;
public class Build { public static class VERSION { public static int SDK_INT; } }''',
            "android/location/LocationListener.java": '''package android.location;
public interface LocationListener {}''',
            "android/location/LocationManager.java": '''package android.location;
public class LocationManager {
  public static final String FUSED_PROVIDER="fused";
  public int requests;
  public void requestLocationUpdates(String provider, LocationRequest request,
      java.util.concurrent.Executor executor, LocationListener listener) {
    if (!FUSED_PROVIDER.equals(provider)) throw new AssertionError("Wrong provider");
    requests++;
  }
}''',
            "android/location/LocationRequest.java": '''package android.location;
public class LocationRequest {
  public static final int QUALITY_BALANCED_POWER_ACCURACY=102;
  public static int builders, quality; public static long minInterval;
  public static class Builder {
    public Builder(long interval) { builders++; }
    public Builder setQuality(int value) { quality=value; return this; }
    public Builder setMinUpdateIntervalMillis(long value) { minInterval=value; return this; }
    public LocationRequest build() { return new LocationRequest(); }
  }
}''',
            "org/chromium/base/ThreadUtils.java": '''package org.chromium.base;
public class ThreadUtils {
  public static Handler getUiThreadHandler() { return new Handler(); }
  public static class Handler { public boolean post(Runnable task) { task.run(); return true; } }
}''',
            "org/chromium/build/annotations/NullMarked.java": '''package org.chromium.build.annotations;
public @interface NullMarked {}''',
            "app/titanium/ext/CustomOSApis.java": '''package app.titanium.ext;
public class CustomOSApis {
  public static boolean supported, accepted; public static int calls;
  public static boolean isCoarseLocationRequestSupported() { calls++; return supported; }
  public static boolean setLocationRequestCoarse(android.location.LocationRequest.Builder b) {
    calls++; return accepted;
  }
}''',
            PACKAGE + "LocationFlow.java": '''package org.chromium.device.geolocation;
import android.location.*;
import android.os.Build;
import app.titanium.ext.CustomOSApis;
public class LocationFlow {
  private static void check(boolean value) { if (!value) throw new AssertionError(); }
  public static void main(String[] args) {
    var manager=new LocationManager(); var listener=new LocationListener() {};
    CustomOSApis.supported=CustomOSApis.accepted=true;
    for (int api : new int[] {28, 30}) {
      Build.VERSION.SDK_INT=api;
      check(!CoarseLocationRequests.isSupported());
      check(!CoarseLocationRequests.requestLocationUpdates(manager, listener));
      check(CustomOSApis.calls==0 && LocationRequest.builders==0 && manager.requests==0);
    }
    for (int api : new int[] {31, 36}) {
      Build.VERSION.SDK_INT=api;
      CustomOSApis.supported=CustomOSApis.accepted=false;
      check(!CoarseLocationRequests.isSupported());
      int previous=manager.requests;
      check(!CoarseLocationRequests.requestLocationUpdates(manager, listener));
      check(manager.requests==previous);
      CustomOSApis.supported=CustomOSApis.accepted=true;
      check(CoarseLocationRequests.isSupported());
      check(CoarseLocationRequests.requestLocationUpdates(manager, listener));
      check(manager.requests==previous+1);
      check(LocationRequest.quality==LocationRequest.QUALITY_BALANCED_POWER_ACCURACY);
      check(LocationRequest.minInterval==0);
    }
    System.out.println("PASS: legacy API guard, unsupported OS fallback and coarse location dispatch");
  }
}''',
        }
        with tempfile.TemporaryDirectory(prefix="argon-coarse-location-") as directory:
            root = Path(directory)
            target = root / TARGET
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(source)
            subprocess.run(["bash", "-e", "-c", commands[0]], cwd=root, check=True)
            for name, contents in adapters.items():
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(contents)
            compiler = ["javac"] if shutil.which("javac") else ["java", "-m", "jdk.compiler/com.sun.tools.javac.Main"]
            result = subprocess.run([*compiler, "-d", directory, *map(str, root.rglob("*.java"))],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            result = subprocess.run(["java", "-ea", "-cp", directory,
                                     "org.chromium.device.geolocation.LocationFlow"],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("PASS: legacy API guard", result.stdout)
