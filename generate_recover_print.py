#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import json
import pprint
import re
import sys
from pathlib import Path
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import urlopen


KLIPPER_CONFIG = Path.home() / "klipper" / "config"
DEFAULT_GCODES_ROOT = Path.home() / "printer_data" / "gcodes"
DEFAULT_OUTPUT = KLIPPER_CONFIG / "recover_data" / "recover_print_generated.cfg"
DEFAULT_MOONRAKER = "http://127.0.0.1:7125"
PREFERRED_HISTORY_STATUSES = (
    "klippy_shutdown",
    "interrupted",
    "klippy_disconnect",
    "error",
    "in_progress",
)


LAYER_CHANGE_RE = re.compile(r"^;\s*LAYER_CHANGE\s*$")
CURA_LAYER_RE = re.compile(r"^;\s*LAYER\s*:\s*-?\d+\s*$", re.IGNORECASE)
Z_COMMENT_RE = re.compile(r"^;\s*Z\s*:\s*([+-]?\d+(?:\.\d+)?)\s*$", re.IGNORECASE)
SIMPLIFY_Z_RE = re.compile(
    r"^;\s*layer\b.*?\bZ\s*=\s*([+-]?\d+(?:\.\d+)?)", re.IGNORECASE
)
TOOL_RE = re.compile(r"^T(\d+)\b")
AXIS_VALUE_RE = re.compile(r"([XYZEFS])([+-]?\d+(?:\.\d+)?)")
M104_RE = re.compile(r"^M104(?:\s+T(\d+))?\s+S([+-]?\d+(?:\.\d+)?)\b", re.IGNORECASE)
M109_RE = re.compile(r"^M109(?:\s+T(\d+))?\s+S([+-]?\d+(?:\.\d+)?)\b", re.IGNORECASE)
M140_RE = re.compile(r"^M140\s+S([+-]?\d+(?:\.\d+)?)\b", re.IGNORECASE)
M190_RE = re.compile(r"^M190\s+S([+-]?\d+(?:\.\d+)?)\b", re.IGNORECASE)
M106_RE = re.compile(r"^M106(?:\s+S([+-]?\d+(?:\.\d+)?))?\b", re.IGNORECASE)
M107_RE = re.compile(r"^M107\b", re.IGNORECASE)
M220_RE = re.compile(r"^M220\b", re.IGNORECASE)
M221_RE = re.compile(r"^M221\b", re.IGNORECASE)
SET_PRESSURE_ADVANCE_RE = re.compile(r"^SET_PRESSURE_ADVANCE\b", re.IGNORECASE)
SET_VELOCITY_LIMIT_RE = re.compile(r"^SET_VELOCITY_LIMIT\b", re.IGNORECASE)
SET_GCODE_OFFSET_RE = re.compile(r"^SET_GCODE_OFFSET\b", re.IGNORECASE)
G92_RE = re.compile(r"^G92\b", re.IGNORECASE)
MOVE_RE = re.compile(r"^G0?1\b", re.IGNORECASE)
START_PRINT_RE = re.compile(r"^(?:START_PRINT|PRINT_START)\b", re.IGNORECASE)
PARAM_RE = re.compile(r"\b([A-Z0-9_]+)=([^\s]+)")


def fail(message: str) -> None:
    print(f"ERROR: {message}", file=sys.stderr)
    raise SystemExit(1)


def normalize_ws(command: str) -> str:
    return " ".join(command.split())


def parse_axis(command: str, axis: str) -> float | None:
    for key, value in AXIS_VALUE_RE.findall(command):
        if key.upper() == axis.upper():
            return float(value)
    return None


def parse_named_float(command: str, *names: str) -> float | None:
    values = {key.upper(): value for key, value in PARAM_RE.findall(command)}
    for name in names:
        raw = values.get(name.upper())
        if raw is None:
            continue
        try:
            return float(raw)
        except ValueError:
            continue
    return None


def moonraker_get(base_url: str, path: str, **query: object) -> dict:
    if query:
        path = f"{path}?{urlencode(query)}"
    url = f"{base_url.rstrip('/')}{path}"
    with urlopen(url, timeout=10) as response:
        payload = json.load(response)
    if isinstance(payload, dict) and "result" in payload and isinstance(payload["result"], dict):
        return payload["result"]
    if isinstance(payload, dict):
        return payload
    fail(f"Moonraker devolvio una respuesta inesperada para {url}")


def select_history_file(base_url: str) -> tuple[str, str]:
    try:
        result = moonraker_get(base_url, "/server/history/list", limit=50, order="desc")
    except URLError as exc:
        fail(
            "No pude consultar Moonraker para buscar el ultimo trabajo interrumpido. "
            f"Detalle: {exc}"
        )
    jobs = result.get("jobs")
    if not isinstance(jobs, list):
        fail("Moonraker no devolvio la lista de trabajos esperada.")
    for status in PREFERRED_HISTORY_STATUSES:
        for job in jobs:
            if job.get("status") == status and job.get("exists", False) and job.get("filename"):
                return str(job["filename"]), status
    fail(
        "No encontre trabajos recientes con estado klippy_shutdown/interrupted/"
        "klippy_disconnect/error/in_progress."
    )


def resolve_gcode_path(
    requested: str | None,
    gcodes_root: Path,
    moonraker_url: str,
    use_last_shutdown: bool,
) -> tuple[Path, str]:
    source = "manual"
    if requested:
        candidate = Path(requested).expanduser()
        if not candidate.is_absolute():
            candidate = (gcodes_root / candidate).expanduser()
        candidate = candidate.resolve()
    else:
        relative_name, status = select_history_file(moonraker_url)
        candidate = (gcodes_root / relative_name).expanduser().resolve()
        source = f"moonraker:{status}"

    try:
        candidate.relative_to(gcodes_root.resolve())
    except ValueError:
        fail(
            "El archivo debe estar dentro de ~/printer_data/gcodes para que Klipper "
            "pueda reanudarlo con M23/M26. Copialo ahi y volve a ejecutar el script."
        )
    if not candidate.is_file():
        fail(f"No existe el archivo G-code: {candidate}")
    if not use_last_shutdown and not requested:
        fail("No se especifico archivo y no se permitio buscarlo en Moonraker.")
    return candidate, source


def build_state_commands(state: dict) -> list[str]:
    commands: list[str] = []
    for key in (
        "units_cmd",
        "xyz_mode_cmd",
        "e_mode_cmd",
        "tool_cmd",
        "speed_factor_cmd",
        "flow_factor_cmd",
        "velocity_limit_cmd",
        "pressure_advance_cmd",
        "gcode_offset_cmd",
        "fan_cmd",
    ):
        value = state.get(key)
        if value:
            commands.append(value)
    return commands


def snapshot_layer(state: dict, offset: int, z_value: float) -> dict:
    return {
        "z": round(z_value, 4),
        "offset": offset,
        "bed": round(float(state["bed_target"]), 3),
        "t0": round(float(state["tool_targets"].get(0, 0.0)), 3),
        "t1": round(float(state["tool_targets"].get(1, 0.0)), 3),
        "xyz_mode": state["xyz_mode_cmd"],
        "e_mode": state["e_mode_cmd"],
        "e_position": round(float(state["absolute_e_position"]), 5),
        "resume_x": None,
        "resume_y": None,
        "travel_f": round(float(state["current_feed"]), 3),
        "state_commands": build_state_commands(state),
    }


def parse_gcode(path: Path) -> list[dict]:
    text = path.read_text(errors="replace")
    lines = text.splitlines()
    byte_offset = 0
    pending_layer_offset: int | None = None
    waiting_z_after_layer = False
    pending_resume_xy_layer: int | None = None
    layers: list[dict] = []
    state = {
        "tool_targets": {0: 0.0, 1: 0.0},
        "bed_target": 0.0,
        "active_tool": 0,
        "units_cmd": "G21",
        "xyz_mode_cmd": "G90",
        "e_mode_cmd": "M82",
        "fan_cmd": "M107",
        "speed_factor_cmd": None,
        "flow_factor_cmd": None,
        "velocity_limit_cmd": None,
        "pressure_advance_cmd": None,
        "gcode_offset_cmd": None,
        "tool_cmd": "T0",
        "absolute_e_position": 0.0,
        "current_x": None,
        "current_y": None,
        "current_feed": 7200.0,
    }

    def record_layer(z_value: float, offset: int) -> None:
        nonlocal pending_resume_xy_layer
        if layers and z_value <= layers[-1]["z"]:
            return
        layers.append(snapshot_layer(state, offset, z_value))
        pending_resume_xy_layer = len(layers) - 1

    for raw_line in lines:
        stripped = raw_line.strip()
        command = normalize_ws(raw_line.split(";", 1)[0].strip())

        if LAYER_CHANGE_RE.match(stripped):
            pending_layer_offset = byte_offset
            waiting_z_after_layer = False
        elif CURA_LAYER_RE.match(stripped):
            pending_layer_offset = byte_offset
            waiting_z_after_layer = True

        match = Z_COMMENT_RE.match(stripped)
        if match and pending_layer_offset is not None:
            record_layer(float(match.group(1)), pending_layer_offset)
            pending_layer_offset = None
            waiting_z_after_layer = False

        match = SIMPLIFY_Z_RE.match(stripped)
        if match:
            record_layer(float(match.group(1)), pending_layer_offset or byte_offset)
            pending_layer_offset = None
            waiting_z_after_layer = False

        if waiting_z_after_layer and command and MOVE_RE.match(command):
            z_value = parse_axis(command, "Z")
            if z_value is not None:
                record_layer(z_value, pending_layer_offset or byte_offset)
                pending_layer_offset = None
                waiting_z_after_layer = False

        match = TOOL_RE.match(command)
        if match:
            active_tool = int(match.group(1))
            state["active_tool"] = active_tool
            state["tool_cmd"] = f"T{active_tool}"

        if command == "G20":
            state["units_cmd"] = "G20"
        elif command == "G21":
            state["units_cmd"] = "G21"
        elif command == "G90":
            state["xyz_mode_cmd"] = "G90"
        elif command == "G91":
            state["xyz_mode_cmd"] = "G91"
        elif command == "M82":
            state["e_mode_cmd"] = "M82"
        elif command == "M83":
            state["e_mode_cmd"] = "M83"
        elif M220_RE.match(command):
            state["speed_factor_cmd"] = command
        elif M221_RE.match(command):
            state["flow_factor_cmd"] = command
        elif SET_VELOCITY_LIMIT_RE.match(command):
            state["velocity_limit_cmd"] = command
        elif SET_PRESSURE_ADVANCE_RE.match(command):
            state["pressure_advance_cmd"] = command
        elif SET_GCODE_OFFSET_RE.match(command):
            state["gcode_offset_cmd"] = command
        elif START_PRINT_RE.match(command):
            bed_value = parse_named_float(command, "BED_TEMP", "BED", "BEDTEMPERATURE")
            if bed_value is not None:
                state["bed_target"] = bed_value
            t0_value = parse_named_float(command, "T0_TEMP", "EXTRUDER", "HOTEND_TEMP", "NOZZLE")
            if t0_value is not None:
                state["tool_targets"][0] = t0_value
            t1_value = parse_named_float(command, "T1_TEMP")
            if t1_value is not None:
                state["tool_targets"][1] = t1_value

        match = M140_RE.match(command)
        if match:
            state["bed_target"] = float(match.group(1))
        match = M190_RE.match(command)
        if match:
            state["bed_target"] = float(match.group(1))

        match = M104_RE.match(command)
        if match:
            tool = int(match.group(1)) if match.group(1) is not None else state["active_tool"]
            if tool in state["tool_targets"]:
                state["tool_targets"][tool] = float(match.group(2))
        match = M109_RE.match(command)
        if match:
            tool = int(match.group(1)) if match.group(1) is not None else state["active_tool"]
            if tool in state["tool_targets"]:
                state["tool_targets"][tool] = float(match.group(2))

        match = M106_RE.match(command)
        if match:
            speed = match.group(1) or "255"
            state["fan_cmd"] = f"M106 S{speed}"
        elif M107_RE.match(command):
            state["fan_cmd"] = "M107"

        if G92_RE.match(command):
            e_value = parse_axis(command, "E")
            if e_value is not None:
                state["absolute_e_position"] = e_value
        elif MOVE_RE.match(command):
            x_value = parse_axis(command, "X")
            y_value = parse_axis(command, "Y")
            f_value = parse_axis(command, "F")
            e_value = parse_axis(command, "E")
            if e_value is not None and state["e_mode_cmd"] == "M82":
                state["absolute_e_position"] = e_value
            if f_value is not None:
                state["current_feed"] = f_value
            if state["xyz_mode_cmd"] == "G90":
                if x_value is not None:
                    state["current_x"] = x_value
                if y_value is not None:
                    state["current_y"] = y_value
            else:
                if x_value is not None and state["current_x"] is not None:
                    state["current_x"] += x_value
                if y_value is not None and state["current_y"] is not None:
                    state["current_y"] += y_value
            if pending_resume_xy_layer is not None and (x_value is not None or y_value is not None):
                layers[pending_resume_xy_layer]["resume_x"] = state["current_x"]
                layers[pending_resume_xy_layer]["resume_y"] = state["current_y"]
                layers[pending_resume_xy_layer]["travel_f"] = round(float(state["current_feed"]), 3)
                pending_resume_xy_layer = None

        byte_offset += len(raw_line.encode()) + 1

    if not layers:
        fail(
            "No pude detectar capas en el G-code. El script soporta marcadores comunes "
            "de Orca/Prusa/SuperSlicer, Cura y Simplify3D."
        )
    return layers


def render_cfg(source_gcode: Path, relative_name: str, source_desc: str, layers: list[dict]) -> str:
    layer_literal = pprint.pformat(layers, width=120, compact=False, sort_dicts=False)
    generated_at = dt.datetime.now().isoformat(timespec="seconds")
    lines = [
        "# Archivo generado automaticamente por generate_recover_print.py",
        f"# Generado: {generated_at}",
        f"# Origen: {source_desc}",
        f"# G-code: {source_gcode}",
        "",
        "[gcode_macro RECOVER_PRINT_EXEC]",
        f"description: Recovery generado para {relative_name}",
        "gcode:",
        "    {% if 'Z' not in params %}",
        "        {action_raise_error(\"Usa: RECOVER_PRINT Z=<altura_nozzle_mm>\")}",
        "    {% endif %}",
        f"    {{% set input_z = params.Z|float %}}",
        f"    {{% set filename = {relative_name!r} %}}",
        "    {% set z_hop = 10.0 %}",
    ]
    for block_line in ("{% set layers = " + layer_literal + " %}").splitlines():
        lines.append(f"    {block_line}")
    lines.extend(
        [
            "",
            "    {% if input_z < layers[0].z %}",
            "        {action_raise_error(\"La Z ingresada esta por debajo de la primera capa imprimible detectada.\")}",
            "    {% endif %}",
            "",
            "    {% set ns = namespace(layer=layers[0]) %}",
            "    {% for layer in layers %}",
            "        {% if input_z >= layer.z %}",
            "            {% set ns.layer = layer %}",
            "        {% endif %}",
            "    {% endfor %}",
            "",
            "    RESPOND MSG=\"Recovery: Z ingresada={input_z} mm, reanudando {filename} desde Z={ns.layer.z} mm.\"",
            "    CLEAR_PAUSE",
            "    SDCARD_RESET_FILE",
            "    M140 S{ns.layer.bed}",
            "    M104 T0 S{ns.layer.t0}",
            "    M104 T1 S{ns.layer.t1}",
            "    M190 S{ns.layer.bed}",
            "    {% if ns.layer.t0 > 0 %}",
            "        M109 T0 S{ns.layer.t0}",
            "    {% endif %}",
            "    {% if ns.layer.t1 > 0 %}",
            "        M109 T1 S{ns.layer.t1}",
            "    {% endif %}",
            "    SET_GCODE_OFFSET X=0 Y=0 Z=0",
            "    G28 X Y",
            "    SET_KINEMATIC_POSITION SET_HOMED=XYZ Z={input_z + z_hop}",
            "    G92 Z{input_z + z_hop}",
            "    {% for command in ns.layer.state_commands %}",
            "        {command}",
            "    {% endfor %}",
            "    G90",
            "    {% if ns.layer.resume_x is not none or ns.layer.resume_y is not none %}",
            "        G1{% if ns.layer.resume_x is not none %} X{ns.layer.resume_x}{% endif %}{% if ns.layer.resume_y is not none %} Y{ns.layer.resume_y}{% endif %} F{ns.layer.travel_f}",
            "    {% endif %}",
            "    G1 Z{ns.layer.z} F600",
            "    M400",
            "    {% if ns.layer.xyz_mode != 'G90' %}",
            "        {ns.layer.xyz_mode}",
            "    {% endif %}",
            "    {% if ns.layer.e_mode == 'M82' %}",
            "        G92 E{ns.layer.e_position}",
            "    {% else %}",
            "        G92 E0",
            "    {% endif %}",
            "    M23 {filename}",
            "    M26 S{ns.layer.offset}",
            "    M24",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Genera recover_data/recover_print_generated.cfg para que la macro "
            "RECOVER_PRINT pueda reanudar un G-code desde una Z dada."
        )
    )
    parser.add_argument(
        "file",
        nargs="?",
        help=(
            "Ruta al G-code dentro de ~/printer_data/gcodes. Si se omite, se intenta "
            "usar el ultimo trabajo interrumpido desde Moonraker."
        ),
    )
    parser.add_argument(
        "--last-shutdown",
        action="store_true",
        help="Fuerza la seleccion automatica del ultimo trabajo interrumpido desde Moonraker.",
    )
    parser.add_argument(
        "--gcodes-root",
        default=str(DEFAULT_GCODES_ROOT),
        help=f"Raiz de virtual_sdcard. Default: {DEFAULT_GCODES_ROOT}",
    )
    parser.add_argument(
        "--moonraker-url",
        default=DEFAULT_MOONRAKER,
        help=f"URL base de Moonraker. Default: {DEFAULT_MOONRAKER}",
    )
    parser.add_argument(
        "--output",
        default=str(DEFAULT_OUTPUT),
        help=f"Archivo cfg de salida. Default: {DEFAULT_OUTPUT}",
    )
    args = parser.parse_args()

    gcodes_root = Path(args.gcodes_root).expanduser().resolve()
    output = Path(args.output).expanduser().resolve()
    use_history = args.last_shutdown or not args.file
    gcode_path, source_desc = resolve_gcode_path(
        args.file,
        gcodes_root=gcodes_root,
        moonraker_url=args.moonraker_url,
        use_last_shutdown=use_history,
    )
    relative_name = gcode_path.relative_to(gcodes_root).as_posix()
    layers = parse_gcode(gcode_path)
    cfg_text = render_cfg(gcode_path, relative_name, source_desc, layers)

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(cfg_text)

    print(f"Archivo analizado: {gcode_path}")
    print(f"Capas detectadas: {len(layers)}")
    print(f"CFG generado: {output}")
    print("Ejecuta RESTART en Klipper y luego usa: RECOVER_PRINT Z=<altura_nozzle_mm>")


if __name__ == "__main__":
    main()
