# MuJoCo H5 → Rerun Viewer

**English** | [한국어](README_KR.md)

These tools display the time series from `stateData.h5` together with a reconstructed MuJoCo scene on one synchronized Rerun timeline. The same commands work on Linux and macOS.

## Installation

Create and activate the Conda environment:

```bash
conda env create -f scripts/rerun/environment.yml
conda activate mujoco-rerun
```

Update an existing environment when the dependency file changes:

```bash
conda env update -f scripts/rerun/environment.yml --prune
conda activate mujoco-rerun
```

## Quick start

For normal use, `view_h5.py` is the only script you need to run:

```bash
python3 scripts/rerun/view_h5.py \
  logs/20260901_152933/stateData.h5 \
  --model model/template/scene.xml
```

It converts the H5 time series, reconstructs the scene with `mj_forward`, merges the recordings, and starts the Web Viewer.

If the H5 path is omitted, the script automatically selects the most recently modified `logs/**/stateData.h5`:

```bash
python3 scripts/rerun/view_h5.py --model model/template/scene.xml
```

The selected recording is printed before conversion:

```text
using latest H5: .../logs/20260901_164916/stateData.h5
```



### Native Viewer

The Native Viewer is recommended for frequent local inspection and larger recordings:

```bash
python3 scripts/rerun/view_h5.py \
  logs/20260901_152933/stateData.h5 \
  --model model/template/scene.xml \
  --native
```



### Build RRD files without opening a viewer

```bash
python3 scripts/rerun/view_h5.py \
  logs/20260901_152933/stateData.h5 \
  --model model/template/scene.xml \
  --no-view
```



### Options

```text
--layout PATH          Blueprint YAML to use
--stride N             Record one out of every N frames
--native               Open the Native Viewer instead of the Web Viewer
--no-view              Build the files without opening a viewer
--rebuild-scene        Force regeneration of the MuJoCo scene cache
--web-port PORT        Web Viewer port (default: 9090)
```

After editing `layout.yaml`, stop the current Viewer and run `view_h5.py` again. The plot recording and Blueprint are always regenerated. The MuJoCo scene recording is reused when the H5 file, top-level MJCF file, and stride have not changed.

Use `--rebuild-scene` when an included XML, mesh, or texture changed without updating the modification time of the top-level scene XML.

## Generated files

The following files are written next to the input H5:

```text
stateData.rrd                  Time series and Blueprint
stateData_mujoco.rrd           MuJoCo scene and per-frame geom transforms
stateData_mujoco.rrd.stride    Stride metadata for the scene cache
stateData_combined.rrd         Final recording opened by the Viewer
```

Open an existing combined recording directly with the Rerun CLI:

```bash
# Native
rerun logs/20260901_152933/stateData_combined.rrd

# Web
rerun --web-viewer logs/20260901_152933/stateData_combined.rrd
```



## Plot and style configuration

The default configuration is `scripts/rerun/layout.yaml`. Dataset paths listed together under `series` are overlaid in one TimeSeries View:

```yaml
plots:
  - name: Base linear velocity — feedback vs command
    series:
      - fbk/pdot_B
      - ctrl/lin_vel_d
```

A missing series is omitted. If none of a plot's series exist in the H5 file, the empty subplot is skipped automatically.

To display only selected vector components, use the mapping form. Indices are zero-based and keep their original numbers in the legend:

```yaml
plots:
  - name: Joint position — feedback vs command
    series:
      - path: fbk/jpos
        indices: [0, 5, 10]
        name: [right knee, right ankle, right hip]
      - path: ctrl/jpos_d
        indices: [0, 5, 10]
        name: [right knee, right ankle, right hip]
```

`name` is optional and must contain one label per selected index. Rerun prefixes these labels with the dataset role, such as `fbk.right knee` and `ctrl.right knee`. The original string form still displays every component.

Error series are calculated while converting and are stored only in the RRD, not in the H5. For example:

```yaml
err:
  - path: err/jpos
    operation: subtract
    lhs: ctrl/jpos_d
    rhs: fbk/jpos
```

This produces `err/jpos = ctrl/jpos_d - fbk/jpos`, which can be used under `plots` like any other series. Currently, `subtract` is the supported operation.

Default styling:

- Component 0/1/2, or x/y/z: red/green/blue
- `fbk/*`: thick solid lines
- `ctrl/*`: point markers using the same component colors
- `param/*`: thin solid lines

Colors, widths, and marker sizes can be changed in `layout.yaml`. Rerun 0.37 does not support dashed patterns for `SeriesLines`.

## Center of mass

The initial 3D camera can be placed in world coordinates. `position` is the camera location and `look_target` is the point it faces; the Viewer remains interactive afterward.

```yaml
scene:
  initial_view:
    enabled: true
    position: [3.5, -3.5, 2.2]
    look_target: [0.0, 0.0, 1.0]
```

The `scene.com` section in `layout.yaml` maps an H5 dataset directly to a CoM sphere:

```yaml
scene:
  com:
    enabled: true
    dataset: fbk/p_CoM
    radius: 0.035
    color: [255, 210, 30]
```

The dataset must contain one 3-vector per sample. Its value is not recomputed by MuJoCo. Set `enabled: false` to hide it.

Contact positions and forces can likewise be mapped directly to arrows:

```yaml
scene:
  hidden_geoms:
    - ground

  contact_forces:
    - name: actual
      positions: fbk/contact_positions
      vectors: fbk/contact_forces
      count: fbk/contact_count
      scale: 0.002
      radius: 0.008
      color: [255, 80, 40]

    - name: desired
      positions: ctrl/contact_positions_d
      vectors: ctrl/contact_forces_d
      count: ctrl/contact_count
      scale: 0.002
      radius: 0.008
      color: [50, 140, 255]
```

Each list item is logged under `/world/contact_forces/<name>`. `positions` and `vectors` accept `(T, N, 3)` or flattened `(T, 3*N)` datasets in world coordinates. `count` is optional when all `N` entries are valid. Each flattened row must be ordered as `[x0, y0, z0, x1, y1, z1, ...]`. `scale` only changes arrow length. `hidden_geoms` suppresses matching MuJoCo geoms in Rerun without changing the simulation model; matching is case-insensitive and accepts glob patterns.

## Inspecting an H5 file

Print dataset names, shapes, dtypes, and compression details:

```bash
python3 scripts/rerun/inspect_h5.py logs/20260901_152933/stateData.h5
```

Scene reconstruction requires the `time` and `fbk/qpos` datasets, and `fbk/qpos.shape[1]` must equal `model.nq` from the selected MJCF.

## File organization

User-facing files:

```text
view_h5.py       Convert, merge, and open the Viewer
inspect_h5.py    Inspect the H5 structure
layout.yaml      Configure the scene, plots, and series styles
environment.yml Define the Conda environment
```

Internal implementation:

```text
rerun_tools.py   H5 conversion, Blueprint, MuJoCo scene, and RRD merging
```



## Current limitation

The current C++ `HDF5Logger` does not write in HDF5 SWMR mode. Reading the file safely from another process while it is still being recorded is therefore not supported. Run `view_h5.py` after the simulation has closed the H5 file.
