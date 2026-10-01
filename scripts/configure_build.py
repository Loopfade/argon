#!/usr/bin/env python3
"""Generate Chromium GN arguments for one supported Android architecture."""
import argparse
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
TARGET_ABIS = {
    "arm64": "arm64-v8a",
    "arm": "armeabi-v7a",
    "x64": "x86_64",
    "x86": "x86",
}


def target_cpu(value: str) -> str:
    """Accept Chromium CPU names and Android ABI names at every entry point."""
    for cpu, abi in TARGET_ABIS.items():
        if value in (cpu, abi):
            return cpu
    raise argparse.ArgumentTypeError(
        f"Unsupported architecture: {value!r}; choose "
        + ", ".join(f"{cpu} ({abi})" for cpu, abi in TARGET_ABIS.items())
    )


def render_gn_args(arch: str, ccache: bool = False, compiler_wrapper=None) -> str:
    cpu = target_cpu(arch)
    if ccache:
        if compiler_wrapper is not None:
            raise ValueError("ccache and compiler_wrapper are mutually exclusive")
        compiler_wrapper = "ccache"
    if compiler_wrapper not in (None, "ccache", "sccache"):
        raise ValueError(f"Unsupported compiler wrapper: {compiler_wrapper!r}")

    # The pinned V8/Vanadium DrumBrake interpreter supports 64-bit targets only.
    drumbrake = "true" if cpu in ("arm64", "x64") else "false"
    overrides = {
        "target_cpu": f'"{cpu}"',
        "v8_enable_drumbrake": drumbrake,
        "v8_drumbrake_bounds_checks": drumbrake,
    }
    result = (ROOT / "args.gn").read_text()
    for name, value in overrides.items():
        result, count = re.subn(
            rf"(?m)^{name}\s*=[^\n]*$", f"{name} = {value}", result
        )
        if count != 1:
            raise ValueError(f"Expected exactly one {name} assignment in args.gn")
    if compiler_wrapper:
        result, count = re.subn(
            r"(?m)^use_siso\s*=[^\n]*$",
            f'use_siso = false\ncc_wrapper = "{compiler_wrapper}"',
            result,
        )
        if count != 1:
            raise ValueError("Expected exactly one use_siso assignment in args.gn")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arch", type=target_cpu, default="arm64")
    wrapper = parser.add_mutually_exclusive_group()
    wrapper.add_argument(
        "--ccache",
        action="store_const",
        const="ccache",
        dest="compiler_wrapper",
        help="Generate a Ninja/ccache configuration for resumable CI builds",
    )
    wrapper.add_argument(
        "--sccache",
        action="store_const",
        const="sccache",
        dest="compiler_wrapper",
        help="Generate a Ninja/sccache configuration for distributed CI builds",
    )
    output = parser.add_mutually_exclusive_group()
    output.add_argument("--print-cpu", action="store_true")
    output.add_argument("--output", type=Path, help="Write args.gn instead of stdout")
    args = parser.parse_args()
    if args.print_cpu:
        print(args.arch)
        return
    rendered = render_gn_args(args.arch, compiler_wrapper=args.compiler_wrapper)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        # Keep timestamps stable across checkpoints and the final signing pass.
        if not args.output.exists() or args.output.read_text() != rendered:
            args.output.write_text(rendered)
    else:
        print(rendered, end="")


if __name__ == "__main__":
    main()
