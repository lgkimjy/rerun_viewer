"""Internal implementation for the H5/MuJoCo Rerun tools."""

from __future__ import annotations

import fnmatch
import math
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class DatasetInfo:
    name: str
    shape: tuple[int, ...]
    dtype: str
    chunks: tuple[int, ...] | None
    compression: str | None


def dependencies():
    try:
        import h5py
        import mujoco
        import numpy as np
        import rerun as rr
        import rerun.blueprint as rrb
        import yaml
    except ImportError as exc:
        raise SystemExit(
            f"Missing dependency: {exc.name}. Run:\n"
            "  conda env update -f scripts/rerun/environment.yml --prune\n"
            "  conda activate mujoco-rerun"
        ) from exc
    return h5py, mujoco, np, rr, rrb, yaml


def normalize(name: str) -> str:
    return name.strip("/")


def list_datasets(h5_file) -> list[DatasetInfo]:
    infos: list[DatasetInfo] = []

    def visitor(name, obj):
        if hasattr(obj, "shape") and hasattr(obj, "dtype"):
            infos.append(
                DatasetInfo(
                    normalize(name),
                    tuple(obj.shape),
                    str(obj.dtype),
                    tuple(obj.chunks) if obj.chunks else None,
                    obj.compression,
                )
            )

    h5_file.visititems(visitor)
    return sorted(infos, key=lambda item: item.name)


def load_layout(yaml, path: Path) -> dict:
    with path.open("r", encoding="utf-8") as stream:
        config = yaml.safe_load(stream) or {}
    if not isinstance(config, dict):
        raise ValueError("layout root must be a YAML mapping")
    return config


def style_for(styles, path: str):
    for style in styles:
        if not isinstance(style, dict) or "match" not in style:
            raise ValueError("each style must contain a 'match' glob")
        if fnmatch.fnmatch(path, str(style["match"])):
            return style
    return None


def parse_series_entry(entry) -> tuple[str, list[int] | None, list[str] | None]:
    if isinstance(entry, str):
        return normalize(entry), None, None
    if not isinstance(entry, dict) or "path" not in entry:
        raise ValueError("each plot series must be a path string or a mapping with 'path'")
    path = normalize(str(entry["path"]))
    raw_indices = entry.get("indices")
    raw_names = entry.get("names", entry.get("name"))
    names = None
    if raw_names is not None:
        if not isinstance(raw_names, list) or not raw_names:
            raise ValueError(f"series names for {path} must be a non-empty YAML list")
        names = [str(name) for name in raw_names]
    if raw_indices is None:
        return path, None, names
    if not isinstance(raw_indices, list) or not raw_indices:
        raise ValueError(f"series indices for {path} must be a non-empty YAML list")
    if any(not isinstance(index, int) or index < 0 for index in raw_indices):
        raise ValueError(f"series indices for {path} must be non-negative integers")
    if len(set(raw_indices)) != len(raw_indices):
        raise ValueError(f"series indices for {path} must not contain duplicates")
    if names is not None and len(names) != len(raw_indices):
        raise ValueError(f"series names and indices for {path} must have the same length")
    return path, raw_indices, names


def plot_series_settings(config: dict) -> tuple[dict[str, list[int]], dict[str, list[str]]]:
    selections = {}
    custom_names = {}
    for plot in config.get("plots", []):
        for entry in plot.get("series", []):
            path, indices, names = parse_series_entry(entry)
            if indices is not None:
                if path in selections and selections[path] != indices:
                    raise ValueError(f"conflicting index selections for series {path}")
                selections[path] = indices
            if names is not None:
                if path in custom_names and custom_names[path] != names:
                    raise ValueError(f"conflicting names for series {path}")
                custom_names[path] = names
    return selections, custom_names


def series_names(
    path: str, dimension: int, indices: list[int], custom_names: list[str] | None
) -> list[str]:
    role = path.split("/", 1)[0]
    if custom_names is not None:
        return [f"{role}.{name}" for name in custom_names]
    if dimension == 3:
        components = ["x", "y", "z"]
    elif dimension == 4 and "quat" in path.lower():
        components = ["x", "y", "z", "w"]
    else:
        components = [str(index) for index in range(dimension)]
    return [f"{role}.{components[index]}" for index in indices]


def log_styles(
    rr,
    config: dict,
    dimensions: dict[str, int],
    selections: dict[str, list[int]],
    custom_names: dict[str, list[str]],
) -> None:
    styles = config.get("styles", [])
    palette = config.get("component_colors", [])
    if not isinstance(styles, list) or not isinstance(palette, list) or not palette:
        raise ValueError("layout requires non-empty 'styles' and 'component_colors' lists")

    for path, dimension in sorted(dimensions.items()):
        style = style_for(styles, path)
        if style is None:
            continue
        indices = selections.get(path, list(range(dimension)))
        colors = [palette[index % len(palette)] for index in indices]
        configured_names = custom_names.get(path)
        if configured_names is not None and len(configured_names) != len(indices):
            raise ValueError(
                f"series names for {path} must match its {len(indices)} displayed components"
            )
        names = series_names(path, dimension, indices, configured_names)
        visible_count = len(indices)
        mode = str(style.get("mode", "lines"))
        if mode == "lines":
            rr.log(
                path,
                rr.SeriesLines(
                    colors=colors,
                    widths=[float(style.get("width", 2.0))] * visible_count,
                    names=names,
                    visible_series=[True] * visible_count,
                ),
                rr.SeriesPoints(visible_series=[False] * visible_count),
                static=True,
            )
        elif mode == "points":
            rr.log(
                path,
                rr.SeriesLines(
                    colors=colors, names=names, visible_series=[False] * visible_count
                ),
                rr.SeriesPoints(
                    colors=colors,
                    names=names,
                    marker_sizes=[float(style.get("marker_size", 3.0))] * visible_count,
                    visible_series=[True] * visible_count,
                ),
                static=True,
            )
        else:
            raise ValueError(f"unsupported style mode {mode!r} for {path}")


def make_blueprint(rrb, config: dict, available: set[str]):
    styles = config.get("styles", [])
    plots = []
    for plot in config.get("plots", []):
        paths = [parse_series_entry(entry)[0] for entry in plot.get("series", [])]
        existing = [path for path in paths if path in available]
        if not existing:
            continue
        missing = [path for path in paths if path not in available]
        if missing:
            print(f"layout: {plot.get('name', 'plot')!r} omits: {', '.join(missing)}")
        overrides = {}
        for path in existing:
            style = style_for(styles, path)
            mode = str(style.get("mode", "lines")) if style else "lines"
            overrides[f"/{path}"] = rrb.Visualizer(
                "SeriesPoints" if mode == "points" else "SeriesLines"
            )
        plots.append(
            rrb.TimeSeriesView(
                name=str(plot.get("name", "Time series")),
                origin="/",
                contents=[f"/{path}" for path in existing],
                overrides=overrides,
                plot_legend=rrb.PlotLegend(visible=True),
            )
        )

    parts = []
    scene = config.get("scene", {})
    if isinstance(scene, dict) and scene.get("enabled", True):
        origin = normalize(str(scene.get("origin", "world")))
        initial_view = scene.get("initial_view", {})
        eye_controls = None
        if isinstance(initial_view, dict) and initial_view.get("enabled", False):
            eye_controls = rrb.EyeControls3D(
                kind=rrb.Eye3DKind.Orbital,
                position=initial_view.get("position", [3.0, -3.0, 2.0]),
                look_target=initial_view.get("look_target", [0.0, 0.0, 1.0]),
                eye_up=[0.0, 0.0, 1.0],
            )
        parts.append(
            rrb.Spatial3DView(
                name=str(scene.get("name", "Robot scene")),
                origin=f"/{origin}",
                contents=[f"/{origin}/**"],
                eye_controls=eye_controls,
            )
        )
    if plots:
        plot_container = rrb.Vertical(contents=plots, name="Signals")
        if parts:
            return rrb.Blueprint(
                rrb.Horizontal(contents=[parts[0], plot_container], column_shares=[2, 1]),
                rrb.BlueprintPanel(state=rrb.PanelState.Collapsed),
                rrb.SelectionPanel(state=rrb.PanelState.Collapsed),
                rrb.TimePanel(
                    timeline="sim_time", play_state="playing", loop_mode="all"
                ),
                auto_views=False,
            )
        parts.append(plot_container)
    if not parts:
        raise ValueError("layout produced no scene or plots")
    return rrb.Blueprint(
        *parts,
        rrb.BlueprintPanel(state=rrb.PanelState.Collapsed),
        rrb.SelectionPanel(state=rrb.PanelState.Collapsed),
        rrb.TimePanel(timeline="sim_time", play_state="playing", loop_mode="all"),
        auto_views=False,
    )


def prepare_error_series(h5_file, np, config: dict, selections: dict[str, list[int]]):
    error_sources = []
    for spec in config.get("err", []):
        if not isinstance(spec, dict):
            raise ValueError("each err entry must be a YAML mapping")
        path = normalize(str(spec.get("path", "")))
        lhs_path = normalize(str(spec.get("lhs", "")))
        rhs_path = normalize(str(spec.get("rhs", "")))
        operation = str(spec.get("operation", "subtract"))
        if not path or not lhs_path or not rhs_path:
            raise ValueError("each err entry requires path, lhs, and rhs")
        if operation != "subtract":
            raise ValueError(f"unsupported err operation {operation!r} for {path}")
        if path in h5_file:
            raise ValueError(f"err path conflicts with an H5 dataset: {path}")
        if lhs_path not in h5_file or rhs_path not in h5_file:
            missing = [p for p in (lhs_path, rhs_path) if p not in h5_file]
            raise ValueError(f"err input dataset not found: {', '.join(missing)}")
        lhs, rhs = h5_file[lhs_path], h5_file[rhs_path]
        if not np.issubdtype(lhs.dtype, np.number) or not np.issubdtype(rhs.dtype, np.number):
            raise ValueError(f"err inputs for {path} must be numeric")
        lhs_dimension = int(np.prod(lhs.shape[1:])) if lhs.ndim > 1 else 1
        rhs_dimension = int(np.prod(rhs.shape[1:])) if rhs.ndim > 1 else 1
        if lhs_dimension != rhs_dimension:
            raise ValueError(
                f"err inputs for {path} have different dimensions: "
                f"{lhs_path}={lhs_dimension}, {rhs_path}={rhs_dimension}"
            )
        indices = selections.get(path)
        if indices is not None and max(indices) >= lhs_dimension:
            raise ValueError(
                f"series index {max(indices)} is out of range for "
                f"{path} with dimension {lhs_dimension}"
            )
        error_sources.append((path, lhs, rhs, lhs_dimension, indices))
    return error_sources


def resolve_sample_count(h5_file) -> int:
    for name in ("fbk/qpos", "fbk/xi_quat", "fbk/p_B", "fbk/jpos"):
        if name in h5_file:
            return int(h5_file[name].shape[0])
    infos = list_datasets(h5_file)
    if not infos or not infos[0].shape:
        raise ValueError("H5 file contains no datasets to infer sample count")
    return int(infos[0].shape[0])


def choose_stride(n: int, dt: float, target_hz: float = 40.0, max_frames: int = 4000) -> int:
    """Keep short clips dense; downsample long 1 kHz logs to ~40 Hz, capped at max_frames."""
    if n <= 1:
        return 1
    duration = n * dt if dt > 0.0 else 0.0
    if duration <= 2.0 and n <= max_frames:
        return 1
    stride = 1
    if dt > 0.0 and target_hz > 0.0:
        src_hz = 1.0 / dt
        if src_hz > target_hz:
            stride = max(1, int(round(src_hz / target_hz)))
    n_out = 1 + (n - 1) // stride
    if n_out > max_frames:
        stride = max(stride, int(math.ceil(n / max_frames)))
    return stride


def choose_stride_for_file(input_path: Path, dt: float) -> tuple[int, int]:
    h5py, _, _, _, _, _ = dependencies()
    with h5py.File(input_path, "r") as h5_file:
        n = resolve_sample_count(h5_file)
    return choose_stride(n, dt), n


def layout_dataset_names(config: dict) -> set[str]:
    names: set[str] = set()
    for plot in config.get("plots", []):
        for entry in plot.get("series", []):
            names.add(parse_series_entry(entry)[0])
    for spec in config.get("err", []):
        if not isinstance(spec, dict):
            continue
        for key in ("path", "lhs", "rhs"):
            value = normalize(str(spec.get(key, "")))
            if value:
                names.add(value)
    return names


def strided_rows(np, dataset, stop: int, stride: int):
    rows = np.asarray(dataset[:stop:stride])
    if rows.ndim == 1:
        return rows.reshape(-1, 1)
    return rows.reshape(rows.shape[0], -1)


def resolve_times(h5_file, np, dt: float):
    for name in ("time", "others/time"):
        if name in h5_file:
            print(f"using H5 time dataset: {name}")
            return np.asarray(h5_file[name], dtype=float).reshape(-1)
    if dt <= 0.0:
        raise ValueError(f"dt must be positive when synthesizing time, got {dt}")
    n = resolve_sample_count(h5_file)
    print(f"H5 has no time dataset; synthesizing t = i * {dt:g} s ({n} samples)")
    return np.arange(n, dtype=float) * dt


def xi_quat_to_qpos(np, xi, nq: int):
    xi = np.asarray(xi, dtype=float)
    if xi.ndim == 1:
        xi = xi.reshape(1, -1)
    if xi.ndim != 2 or xi.shape[1] < 7:
        raise ValueError(f"fbk/xi_quat shape {xi.shape} must be (T, >=7)")
    n, width = xi.shape
    qpos = np.zeros((n, nq), dtype=float)
    qpos[:, 0:3] = xi[:, 0:3]
    quat = xi[:, 3:7].copy()
    nrm = np.linalg.norm(quat, axis=1, keepdims=True)
    nrm = np.where(nrm > 1e-12, nrm, 1.0)
    qpos[:, 3:7] = quat / nrm
    if nq == width:
        qpos[:, 7:] = xi[:, 7:]
        return qpos
    # G1 29-DoF + Inspire hands: left-hand joints sit between the left and right arms.
    if nq >= 60 and width >= 36:
        qpos[:, 7:29] = xi[:, 7:29]
        qpos[:, 41:48] = xi[:, 29:36]
        return qpos
    if nq > 7:
        n_joints = min(width - 7, nq - 7)
        qpos[:, 7 : 7 + n_joints] = xi[:, 7 : 7 + n_joints]
        return qpos
    raise ValueError(f"cannot map xi_quat width {width} to model.nq={nq}")


def resolve_qpos(h5_file, np, nq: int, index=slice(None)):
    if "fbk/qpos" in h5_file:
        qpos = np.asarray(h5_file["fbk/qpos"][index], dtype=float)
        return qpos.reshape(1, -1) if qpos.ndim == 1 else qpos
    if "fbk/xi_quat" not in h5_file:
        raise ValueError("scene conversion requires fbk/qpos or fbk/xi_quat")
    xi = np.asarray(h5_file["fbk/xi_quat"][index], dtype=float)
    print(f"H5 has no fbk/qpos; mapping fbk/xi_quat {tuple(np.shape(xi))} -> qpos (*, {nq})")
    return xi_quat_to_qpos(np, xi, nq)


def convert_plots(input_path: Path, output: Path, layout: Path, stride: int, dt: float = 0.001) -> None:
    h5py, _, np, rr, rrb, yaml = dependencies()
    config = load_layout(yaml, layout)
    selections, custom_names = plot_series_settings(config)
    with h5py.File(input_path, "r") as h5_file:
        # if "time" not in h5_file:
        #     raise ValueError("dataset not found: time")
        # times = h5_file["time"]
        times = resolve_times(h5_file, np, dt)
        wanted = layout_dataset_names(config)
        datasets = []
        for info in list_datasets(h5_file):
            if info.name == "time" or not info.shape or len(info.shape) > 2:
                continue
            if wanted and info.name not in wanted:
                continue
            dataset = h5_file[info.name]
            if np.issubdtype(dataset.dtype, np.number):
                dimension = int(dataset.shape[1]) if len(dataset.shape) > 1 else 1
                indices = selections.get(info.name)
                if indices is not None and max(indices) >= dimension:
                    raise ValueError(
                        f"series index {max(indices)} is out of range for "
                        f"{info.name} with dimension {dimension}"
                    )
                datasets.append((info, dataset, indices))
        dimensions = {
            info.name: (int(dataset.shape[1]) if len(dataset.shape) > 1 else 1)
            for info, dataset, _ in datasets
        }
        error_sources = prepare_error_series(h5_file, np, config, selections)
        dimensions.update(
            {path: dimension for path, _, _, dimension, _ in error_sources}
        )
        lengths = [len(times), *(len(dataset) for _, dataset, _ in datasets)]
        lengths.extend(len(source) for _, lhs, rhs, _, _ in error_sources for source in (lhs, rhs))
        stop = min(lengths) if lengths else 0

        rr.init(input_path.stem, recording_id=input_path.resolve().as_posix())
        rr.save(output)
        log_styles(rr, config, dimensions, selections, custom_names)
        times_kept = np.asarray(times[:stop:stride], dtype=float).reshape(-1)
        loaded = []
        for info, dataset, indices in datasets:
            rows = strided_rows(np, dataset, stop, stride)
            if indices is not None:
                rows = rows[:, indices]
            loaded.append((info.name, rows))
        loaded_errors = []
        for path, lhs, rhs, _, indices in error_sources:
            rows = strided_rows(np, lhs, stop, stride) - strided_rows(np, rhs, stop, stride)
            if indices is not None:
                rows = rows[:, indices]
            loaded_errors.append((path, rows))
        n_frames = len(times_kept)
        # for index in range(0, stop, stride):
        #     time_row = np.asarray(times[index]).reshape(-1)
        #     ...
        #     row = np.asarray(dataset[index]).reshape(-1)
        for frame in range(n_frames):
            rr.set_time("sim_time", timestamp=float(times_kept[frame]))
            for name, rows in loaded:
                if rows.shape[0] <= frame:
                    continue
                row = np.asarray(rows[frame]).reshape(-1)
                if row.size:
                    rr.log(name, rr.Scalars(row.astype(float, copy=False)))
            for path, rows in loaded_errors:
                if rows.shape[0] <= frame:
                    continue
                row = np.asarray(rows[frame]).reshape(-1)
                rr.log(path, rr.Scalars(row.astype(float, copy=False)))
        rr.send_blueprint(make_blueprint(rrb, config, set(dimensions)))
    rr.disconnect()
    print(f"wrote {output.resolve()} ({n_frames} samples)")


def geom_path(mujoco, model, geom_id: int) -> str:
    name = mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_GEOM, geom_id)
    return f"world/geoms/{name or f'geom_{geom_id}'}"


def geom_is_hidden(mujoco, model, geom_id: int, patterns: list[str]) -> bool:
    name = mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_GEOM, geom_id)
    candidates = [name or f"geom_{geom_id}", geom_path(mujoco, model, geom_id)]
    return any(
        fnmatch.fnmatch(candidate.lower(), pattern.lower())
        for candidate in candidates
        for pattern in patterns
    )


def log_geom(rr, np, mujoco, model, geom_id: int) -> bool:
    path = geom_path(mujoco, model, geom_id)
    kind = int(model.geom_type[geom_id])
    size = np.asarray(model.geom_size[geom_id], dtype=float)
    color = np.clip(np.asarray(model.geom_rgba[geom_id]) * 255, 0, 255).astype(np.uint8)
    solid = rr.components.FillMode.Solid
    if kind == int(mujoco.mjtGeom.mjGEOM_PLANE):
        half = [size[0] or 5.0, size[1] or 5.0, 0.005]
        shape = rr.Boxes3D(half_sizes=[half], colors=[color], fill_mode=solid)
    elif kind == int(mujoco.mjtGeom.mjGEOM_BOX):
        shape = rr.Boxes3D(half_sizes=[size[:3]], colors=[color], fill_mode=solid)
    elif kind in (int(mujoco.mjtGeom.mjGEOM_SPHERE), int(mujoco.mjtGeom.mjGEOM_ELLIPSOID)):
        half = [size[0]] * 3 if kind == int(mujoco.mjtGeom.mjGEOM_SPHERE) else size[:3]
        shape = rr.Ellipsoids3D(half_sizes=[half], colors=[color], fill_mode=solid)
    elif kind == int(mujoco.mjtGeom.mjGEOM_CYLINDER):
        shape = rr.Cylinders3D(
            radii=[size[0]], lengths=[2 * size[1]], colors=[color], fill_mode=solid
        )
    elif kind == int(mujoco.mjtGeom.mjGEOM_CAPSULE):
        shape = rr.Capsules3D(radii=[size[0]], lengths=[2 * size[1]], colors=[color])
    elif kind == int(mujoco.mjtGeom.mjGEOM_MESH):
        mesh_id = int(model.geom_dataid[geom_id])
        va, vn = int(model.mesh_vertadr[mesh_id]), int(model.mesh_vertnum[mesh_id])
        na, nn = int(model.mesh_normaladr[mesh_id]), int(model.mesh_normalnum[mesh_id])
        fa, fn = int(model.mesh_faceadr[mesh_id]), int(model.mesh_facenum[mesh_id])
        normals = None
        if nn == vn:
            normals = np.asarray(model.mesh_normal[na : na + nn]).copy()
        shape = rr.Mesh3D(
            vertex_positions=np.asarray(model.mesh_vert[va : va + vn]).copy(),
            vertex_normals=normals,
            triangle_indices=np.asarray(model.mesh_face[fa : fa + fn]).copy(),
            albedo_factor=color,
        )
    else:
        print(f"warning: unsupported geom type {kind} at {path}")
        return False
    rr.log(path, shape, static=True)
    return True


def scene_dataset(h5_file, config: dict, key: str):
    path = normalize(str(config.get(key, "")))
    if not path:
        raise ValueError(f"scene dataset path is empty: {key}")
    if path not in h5_file:
        raise ValueError(f"scene dataset not found: {path}")
    return h5_file[path]


def vector3_row(np, dataset, index: int):
    row = np.asarray(dataset[index], dtype=float)
    if row.size % 3:
        raise ValueError(
            f"scene dataset {dataset.name} row width must be divisible by 3, got {row.size}"
        )
    return row.reshape(-1, 3)


def convert_scene(
    input_path: Path, model_path: Path, output: Path, layout_path: Path, stride: int, dt: float = 0.001
) -> None:
    h5py, mujoco, np, rr, _, yaml = dependencies()
    config = load_layout(yaml, layout_path)
    scene_config = config.get("scene", {})
    if not isinstance(scene_config, dict):
        raise ValueError("'scene' must be a YAML mapping")
    model = mujoco.MjModel.from_xml_path(str(model_path.resolve()))
    data = mujoco.MjData(model)
    with h5py.File(input_path, "r") as h5_file:
        # if "fbk/qpos" not in h5_file or "time" not in h5_file:
        #     raise ValueError("scene conversion requires time and fbk/qpos")
        # qpos, times = h5_file["fbk/qpos"], h5_file["time"]
        times = resolve_times(h5_file, np, dt)
        if "fbk/qpos" in h5_file:
            n_qpos = int(h5_file["fbk/qpos"].shape[0])
        elif "fbk/xi_quat" in h5_file:
            n_qpos = int(h5_file["fbk/xi_quat"].shape[0])
        else:
            raise ValueError("scene conversion requires fbk/qpos or fbk/xi_quat")
        rr.init(input_path.stem, recording_id=input_path.resolve().as_posix())
        rr.save(output)
        rr.log("world", rr.ViewCoordinates.RIGHT_HAND_Z_UP, static=True)
        hidden_geoms = scene_config.get("hidden_geoms", [])
        if not isinstance(hidden_geoms, list):
            raise ValueError("scene.hidden_geoms must be a YAML list")
        hidden_geoms = [str(pattern) for pattern in hidden_geoms]
        geoms = [
            geom_id
            for geom_id in range(model.ngeom)
            if not geom_is_hidden(mujoco, model, geom_id, hidden_geoms)
            and log_geom(rr, np, mujoco, model, geom_id)
        ]

        com_config = scene_config.get("com", {})
        com_enabled = isinstance(com_config, dict) and com_config.get("enabled", False)
        com_dataset = (
            scene_dataset(h5_file, com_config, "dataset") if com_enabled else None
        )

        force_configs = scene_config.get("contact_forces", [])
        if not isinstance(force_configs, list):
            raise ValueError("scene.contact_forces must be a YAML list")
        force_sources = []
        for force_config in force_configs:
            if not isinstance(force_config, dict):
                raise ValueError("each contact force entry must be a YAML mapping")
            if not force_config.get("enabled", False):
                continue
            name = normalize(str(force_config.get("name", "contact")))
            positions_dataset = scene_dataset(h5_file, force_config, "positions")
            vectors_dataset = scene_dataset(h5_file, force_config, "vectors")
            count_path = normalize(str(force_config.get("count", "")))
            count_dataset = h5_file[count_path] if count_path and count_path in h5_file else None
            if count_path and count_dataset is None:
                raise ValueError(f"scene dataset not found: {count_path}")
            force_sources.append(
                (name, force_config, positions_dataset, vectors_dataset, count_dataset)
            )

        stop = min(len(times), n_qpos)
        scene_datasets = [
            dataset
            for dataset in (
                com_dataset,
                *(
                    dataset
                    for _, _, positions, vectors, count in force_sources
                    for dataset in (positions, vectors, count)
                ),
            )
            if dataset is not None
        ]
        if scene_datasets:
            stop = min(stop, *(len(dataset) for dataset in scene_datasets))
        # qpos = resolve_qpos(h5_file, np, int(model.nq))
        qpos = resolve_qpos(h5_file, np, int(model.nq), slice(0, stop, stride))
        if qpos.ndim != 2 or qpos.shape[1] != model.nq:
            raise ValueError(f"qpos shape {qpos.shape} does not match model.nq={model.nq}")
        kept_i = 0
        for index in range(0, stop, stride):
            sim_time = float(np.asarray(times[index]).reshape(-1)[0])
            rr.set_time("sim_time", timestamp=sim_time)
            data.qpos[:] = qpos[kept_i]
            kept_i += 1
            mujoco.mj_forward(model, data)
            for geom_id in geoms:
                rr.log(
                    geom_path(mujoco, model, geom_id),
                    rr.Transform3D(
                        translation=np.asarray(data.geom_xpos[geom_id]),
                        mat3x3=np.asarray(data.geom_xmat[geom_id]).reshape(3, 3),
                    ),
                )
            if com_enabled:
                com = vector3_row(np, com_dataset, index)
                if len(com) != 1:
                    raise ValueError(
                        f"CoM dataset {com_dataset.name} must contain one 3-vector per row"
                    )
                rr.log(
                    "world/debug/com",
                    rr.Points3D(
                        positions=com,
                        radii=[float(com_config.get("radius", 0.035))],
                        colors=[com_config.get("color", [255, 210, 30])],
                    ),
                )
            for name, force_config, positions_dataset, vectors_dataset, count_dataset in force_sources:
                positions = vector3_row(np, positions_dataset, index)
                vectors = vector3_row(np, vectors_dataset, index)
                if len(positions) != len(vectors):
                    raise ValueError(
                        "contact position and force rows must contain the same number of vectors"
                    )
                count = len(positions)
                if count_dataset is not None:
                    count_row = np.asarray(count_dataset[index]).reshape(-1)
                    if count_row.size != 1:
                        raise ValueError(
                            f"contact count dataset {count_dataset.name} must have one value per row"
                        )
                    count = int(count_row[0])
                    if not 0 <= count <= len(positions):
                        raise ValueError(
                            f"contact count {count} is outside [0, {len(positions)}] at row {index}"
                        )
                rr.log(
                    f"world/contact_forces/{name}",
                    rr.Arrows3D(
                        origins=positions[:count],
                        vectors=vectors[:count] * float(force_config.get("scale", 1.0)),
                        colors=[force_config.get("color", [255, 80, 40])],
                        radii=[float(force_config.get("radius", 0.008))],
                    ),
                )
    rr.disconnect()
    print(f"wrote {output.resolve()} ({len(range(0, stop, stride))} frames)")


def rerun_executable() -> str:
    executable = shutil.which("rerun")
    sibling = Path(sys.executable).with_name("rerun")
    if executable:
        return executable
    if sibling.is_file():
        return str(sibling)
    raise SystemExit("rerun executable not found in the active Conda environment")


def merge_rrds(inputs: list[Path], output: Path) -> None:
    result = subprocess.call([rerun_executable(), "rrd", "merge", "-o", str(output), *map(str, inputs)])
    if result:
        raise SystemExit(result)
    print(f"wrote {output.resolve()}")


def open_viewer(recording: Path, native: bool, web_port: int) -> int:
    command = [rerun_executable()]
    if not native:
        command += ["--web-viewer", "--web-viewer-port", str(web_port)]
    command.append(str(recording))
    try:
        return subprocess.call(command)
    except KeyboardInterrupt:
        return 130
