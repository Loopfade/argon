"""Prevent foreign NDK headers and unstable system-module cache dependencies."""
import importlib.util
import itertools
from pathlib import Path
import re
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("modulemap_patch", ROOT / "scripts/apply_modulemap.py")
modulemap = importlib.util.module_from_spec(spec)
spec.loader.exec_module(modulemap)

TRIPLES = {
    "arm": "arm-linux-androideabi", "arm64": "aarch64-linux-android",
}
FOREIGN_TRIPLES = ("i686-linux-android", "x86_64-linux-android", "riscv64-linux-android")


def verify_pinned_generator(src):
    """Exercise the patched upstream discovery/rendering, stubbing only Clang."""
    config_spec = importlib.util.spec_from_file_location("modulemap_config", src / modulemap.CONFIG)
    config = importlib.util.module_from_spec(config_spec)
    generator_spec = importlib.util.spec_from_file_location("pinned_modulemap", src / modulemap.GENERATOR)
    generator = importlib.util.module_from_spec(generator_spec)
    with patch.dict(sys.modules, {"modulemap_config": config, "pinned_modulemap": generator}):
        config_spec.loader.exec_module(config)
        generator_spec.loader.exec_module(generator)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            include = root / "third_party/android_toolchain/ndk/toolchains/llvm/prebuilt/linux-x86_64/sysroot/usr/include"
            directories = [include / triple for triple in (*TRIPLES.values(), *FOREIGN_TRIPLES)]
            for directory in directories:
                (directory / "asm").mkdir(parents=True)
                for name in ("hwcap.h", "ptrace.h"):
                    (directory / "asm" / name).write_text("// pinned header\n")
            common = include / "stdint.h"
            common.write_text("// common header\n")
            output = root / "out/Argon/gen/build/modules/module.modulemap"
            output.parent.mkdir(parents=True)
            allowlist = generator.AllowList([
                config.AllowedHeader("asm/hwcap.h"), config.AllowedHeader("asm/ptrace.h"),
            ])
            for cpu, triple in TRIPLES.items():
                target = include / triple
                dependencies = [common, target / "asm/hwcap.h", target / "asm/ptrace.h"]

                def compiler(command, check):
                    depfile = Path(command[command.index("-MF") + 1])
                    depfile.write_text("dummy.o: " + " ".join(map(str, dependencies)))
                    return types.SimpleNamespace(returncode=0)

                reference = None
                for order in itertools.permutations(directories):
                    with patch.object(generator.subprocess, "run", compiler):
                        headers = generator.calculate_transitive_headers(
                            clang_args=["fixture-clang"], include_dirs=[],
                            sysroot_dirs=[include, *order], allowlist=allowlist,
                            target_os="android", target_cpu=cpu,
                        )
                    if {header.path for header in headers} != set(dependencies):
                        raise ValueError(f"Foreign or missing NDK headers for {cpu}: {headers}")
                    rendered = generator.combine_modulemaps(
                        output, [], headers, "//build/modules:system", [], {}, set(),
                    )
                    if reference is not None and rendered != reference:
                        raise ValueError(f"System modulemap depends on NDK directory order: {cpu}")
                    reference = rendered
    print("PASS: pinned system module generator; two Android CPUs, 120 NDK directory orders each")


class ModulemapPatchTests(unittest.TestCase):
    def selector(self, text=None):
        source = "def select(sysroot_dirs, target_os, target_cpu):\n" + modulemap.ANCHOR + "    return sysroot_dirs\n"
        namespace = {"_TRIPLE": re.compile(r".*-linux-(?:android|androideabi)$")}
        exec(modulemap.patch_generator(source) if text is None else text, namespace)
        return namespace["select"]

    def test_patch_is_idempotent_and_rejects_changed_or_duplicate_anchors(self):
        source = "def select(sysroot_dirs, target_os, target_cpu):\n" + modulemap.ANCHOR + "    return sysroot_dirs\n"
        patched = modulemap.patch_generator(source)
        self.assertEqual(modulemap.patch_generator(patched), patched)
        for invalid in ("upstream changed", source + source,
                        patched.replace("directory.name == triple", "True")):
            with self.subTest(invalid=invalid[:50]), self.assertRaises(ValueError):
                modulemap.patch_generator(invalid)

    def test_only_target_triple_and_common_headers_are_selected(self):
        select = self.selector()
        with tempfile.TemporaryDirectory() as temporary:
            include = Path(temporary) / "include"
            directories = [include / triple for triple in (*TRIPLES.values(), *FOREIGN_TRIPLES)]
            for directory in directories:
                directory.mkdir(parents=True)
            for cpu, triple in TRIPLES.items():
                for order in (directories, list(reversed(directories))):
                    with self.subTest(cpu=cpu, order=order):
                        self.assertEqual(select([include, *order], "android", cpu), [include, include / triple])
            self.assertEqual(select([include, *directories], "linux", "x64"), [include, *directories])
            for cpu in ("x86", "x64", "riscv64", "unknown"):
                with self.subTest(cpu=cpu), self.assertRaisesRegex(ValueError, "Unsupported Android CPU"):
                    select([include, *directories], "android", cpu)
            with self.assertRaisesRegex(ValueError, "Missing Android sysroot"):
                select([include, directories[0]], "android", "arm64")

    def test_patch_is_available_after_the_cached_source_layer(self):
        dockerfile = (ROOT / "docker/chromium/Dockerfile").read_text()
        self.assertLess(dockerfile.index("bash scripts/prepare_chromium.sh sources"),
                        dockerfile.index("scripts/apply_modulemap.py"))
        self.assertLess(dockerfile.index("scripts/apply_modulemap.py"),
                        dockerfile.index("RUN bash scripts/prepare_chromium.sh patch"))
        self.assertIn('python3 "$SCRIPT_DIR/scripts/apply_modulemap.py" --chromium-src "$PWD"',
                      (ROOT / "patch.sh").read_text())


if __name__ == "__main__":
    unittest.main()
