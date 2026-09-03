#!/usr/bin/env python3
"""Convert an H5 log, reconstruct its MuJoCo scene, and open Rerun."""

from __future__ import annotations

import argparse
from pathlib import Path

from rerun_tools import choose_stride_for_file, convert_plots, convert_scene, merge_rrds, open_viewer


SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent.parent


def stale(output: Path, inputs: list[Path]) -> bool:
    return not output.is_file() or any(
        path.stat().st_mtime > output.stat().st_mtime for path in inputs
    )


def latest_h5() -> Path:
    candidates = [path for path in (PROJECT_ROOT / "logs").glob("**/g1_log.h5") if path.is_file()]
    if not candidates:
        raise SystemExit(
            f"No g1_log.h5 found below {PROJECT_ROOT / 'logs'}. "
            "Pass an H5 path explicitly."
        )
    selected = max(candidates, key=lambda path: path.stat().st_mtime)
    print(f"using latest H5: {selected}")
    return selected


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "input",
        nargs="?",
        type=Path,
        help="g1_log.h5; defaults to the newest logs/**/g1_log.h5",
    )
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--layout", type=Path, default=SCRIPT_DIR / "layout.yaml")
    parser.add_argument(
        "--stride",
        type=int,
        default=None,
        help="Keep 1 of every N samples. Default: auto (~40 Hz, cap 4000 frames)",
    )
    parser.add_argument(
        "--dt",
        type=float,
        default=0.001,
        help="Sample period [s] used when the H5 file has no time dataset (default: 0.001)",
    )
    parser.add_argument("--rebuild-scene", action="store_true")
    parser.add_argument("--native", action="store_true")
    parser.add_argument("--no-view", action="store_true")
    parser.add_argument("--web-port", type=int, default=9090)
    args = parser.parse_args()
    if args.input is None:
        args.input = latest_h5()

    for label, path in (("H5", args.input), ("model", args.model), ("layout", args.layout)):
        if not path.is_file():
            parser.error(f"{label} file not found: {path}")
    if args.dt <= 0.0:
        parser.error("--dt must be > 0")
    if args.stride is None:
        args.stride, n_samples = choose_stride_for_file(args.input, args.dt)
        n_frames = len(range(0, n_samples, args.stride))
        print(f"auto stride={args.stride} ({n_samples} samples -> {n_frames} frames)")
    elif args.stride < 1:
        parser.error("--stride must be >= 1")

    plots = args.input.with_suffix(".rrd")
    scene = args.input.with_name(args.input.stem + "_mujoco.rrd")
    combined = args.input.with_name(args.input.stem + "_combined.rrd")
    stamp = scene.with_suffix(scene.suffix + ".stride")

    convert_plots(args.input, plots, args.layout, args.stride, args.dt)
    cached_stride = stamp.read_text().strip() if stamp.is_file() else None
    if (
        args.rebuild_scene
        or stale(scene, [args.input, args.model, args.layout])
        or cached_stride != str(args.stride)
    ):
        convert_scene(args.input, args.model, scene, args.layout, args.stride, args.dt)
        stamp.write_text(str(args.stride), encoding="utf-8")
    else:
        print(f"reusing scene cache: {scene}")
    merge_rrds([plots, scene], combined)

    if args.no_view:
        print(f"ready: {combined.resolve()}")
        return 0
    return open_viewer(combined, args.native, args.web_port)


if __name__ == "__main__":
    raise SystemExit(main())
