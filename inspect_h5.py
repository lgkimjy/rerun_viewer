#!/usr/bin/env python3
"""Print the datasets and storage characteristics of a stateData.h5 file."""

from __future__ import annotations

import argparse
from pathlib import Path

from rerun_tools import list_datasets


def human_bytes(value: int) -> str:
    size = float(value)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if size < 1024 or unit == "TiB":
            return f"{size:.2f} {unit}"
        size /= 1024
    raise AssertionError("unreachable")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    args = parser.parse_args()

    try:
        import h5py
        import numpy as np
    except ImportError as exc:
        parser.error(
            f"missing {exc.name}; create and activate scripts/rerun/environment.yml"
        )

    with h5py.File(args.input, "r") as h5_file:
        infos = list_datasets(h5_file)
        logical = 0
        print(f"file: {args.input.resolve()}")
        print(f"disk: {human_bytes(args.input.stat().st_size)}")
        print("dataset                                  shape          dtype      compression")
        print("---------------------------------------  -------------  ---------  -----------")
        for info in infos:
            count = int(np.prod(info.shape, dtype=np.int64)) if info.shape else 0
            logical += count * np.dtype(info.dtype).itemsize
            print(
                f"{info.name[:39]:39}  {str(info.shape):13}  "
                f"{info.dtype:9}  {info.compression or '-'}"
            )
        print(f"logical uncompressed payload: {human_bytes(logical)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
