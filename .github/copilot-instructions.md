# Copilot instructions

## Printer hardware (Hellbot Hidra Plus)

**Identidad:** Hellbot Hidra Plus = rebranding argentino de la Tenlog TL-D3 Plus. El config base proviene de `jonpackard/klipper_config_tld3pro` (TL-D3 Pro) y se está adaptando iterativamente. Muchos valores aún reflejan esa máquina y pueden necesitar ajuste.

**Cinemática:** IDEX (Independent Dual EXtruder) — 2 cabezales independientes en el eje X.

**Board:** ATMega2560 (compatible Arduino Mega). Los pines se referencian como `arNN`; el mapeo completo está en `[board_pins arduino-mega]` de `printer.cfg`. USB serial: `/dev/serial/by-id/usb-1a86_USB_Serial-if00-port0`.

**Extrusores:**
| | T0 (izquierdo) | T1 (derecho) |
|---|---|---|
| Macro de selección | `T0` / `SET_DUAL_CARRIAGE CARRIAGE=0` | `T1` / `SET_DUAL_CARRIAGE CARRIAGE=1` |
| Heater pin | ar11 | ar10 |
| Sensor temp | analog15 | analog13 |
| Presure advance | 0.1 | 0.1 |
| Nozzle (actual) | 0.6 mm | 0.6 mm |

**Eje X / Dual carriage:**
- Carro principal (`stepper_x`): endstop `^!ar3`, `position_endstop: -53`, `position_max: 300`
- Carro secundario (`dual_carriage`): endstop `^!ar2`, `position_endstop: 359.35`, `position_max: 360`
- T1 tiene offset calibrado en el macro `T1`: `X=3.80 Y=0.50` (ajustar según impresión real)

**Eje Y:** endstop `^!ar14`, `position_max: 300`

**Eje Z (doble motor, 2 endstops físicos):**
- `stepper_z`: endstop físico `^!ar18` (microswitch inferior izquierdo), `position_endstop: -0.15` (a calibrar), `position_max: 350`
- `stepper_z1`: endstop físico `^!ar19` (microswitch inferior derecho)
- Los 2 endstops independientes reemplazan la función del `z_tilt`: cada motor baja hasta su propio switch → gantry nivelado mecánicamente
- **Sin probe de bed mesh** → `[probe]`, `[z_tilt]` y `[bed_mesh]` están deshabilitados

**Cama:** 300×300 mm aprox. Sensor: `EPCOS 100K B57560G104F` en analog14. Heater: ar8.

**Fans:** Part cooling: ar9. Heater fan: ar5 (enciende cuando cualquier extrusor supera 80°C).

**Slicer:** SuperSlicer. El start gcode esperado para `PREP_PRINT` es:
```
prep_print EXTRUDER={...} BED={...} CHAMBER={...} FILAMENT={...} COUNT={...} TOOLS={...} NUM=1
```
El macro alternativo `START_PRINT` asume que las temperaturas ya fueron seteadas por el slicer.

**Historial de cambios / problemas resueltos:**
- `max_accel_to_decel` → reemplazado por `minimum_cruise_ratio: 0.5` (opción deprecada en Klipper moderno)
- Z homing: revertido de `probe:z_virtual_endstop` a endstops físicos `^!ar18` / `^!ar19`
- Deshabilitados `[probe]`, `[z_tilt]`, `[bed_mesh]` y macros relacionados (probeon, probeoff, bedmesh, BED_MESH_LOAD, bedmesh_renew) — la impresora no tiene probe de bed mesh

**Pendiente de calibrar / verificar:**
- Calibrar `position_endstop` de Z (valor actual -0.15 es del TL-D3 Pro)
- Verificar `rotation_distance` de los extrusores con el hardware real
- Calibrar offset X/Y entre cabezales (actualmente X=3.80, Y=0.50 en macro T1)
- Input shaper (comentado en `printer.cfg`)

## Commands

- There are no repo-local build, test, or lint commands in this repository.
- `klipper/.config` is only a reference snapshot for the upstream Klipper firmware target. If firmware settings need to be regenerated, do that in the upstream `~/klipper` checkout with `make menuconfig` rather than treating this repository as a buildable firmware project.

## High-level architecture

- This repository is an iterative Klipper + Moonraker porting workspace whose real target is a working `printer.cfg` for a Hellbot Hidra Plus, which is a rebrand of the Tenlog TL-D3 Plus. Treat the current config as a baseline under refinement, not as a finished source of truth.
- The current baseline came from the public Tenlog/TL-D3 configuration work in `jonpackard/klipper_config_tld3pro`, so some values still reflect that upstream machine and need to be evaluated against the actual Hellbot hardware.
- `printer.cfg` is the entrypoint and composition root. It includes `kiauh_macros.cfg`, `macros.cfg`, `variables.cfg`, `PREP_PRINT.cfg`, and one active extruder profile. At the moment the active include is `extruders_std/printer.cfg`; the Titan-related includes are present but commented out.
- `printer.cfg` owns machine-level wiring and motion settings: MCU/board pin aliases, cartesian + dual-carriage kinematics, steppers, Z tilt, probe, bed mesh defaults, heaters, fans, idle timeout, and filament/runout handling.
- `macros.cfg` is where most behavior lives: tool parking and switching (`T0`/`T1`), carriage range/offset logic, priming, print start/end flows, bed mesh creation/loading, and helper introspection macros.
- `PREP_PRINT.cfg` is a second start-of-print path that expects slicer parameters such as `EXTRUDER`, `BED`, `COUNT`, and `TOOLS` (the header shows the intended SuperSlicer invocation). Keep it distinct from `START_PRINT`, which assumes heater targets were already set by the slicer.
- `variables.cfg` stores persisted macro state via `[save_variables]`.
- `moonraker.conf` is separate service configuration for Moonraker, including authorization and update-manager entries for Mainsail and Fluidd.
- `extruders_titan/` contains alternate hardware-specific snippets for Titan/heatblock combinations. Those snippets override only the parts that differ from the default profile and are meant to be swapped in via `printer.cfg` includes.

## Key conventions

- Do not hand-edit the `#*# <---------------------- SAVE_CONFIG ---------------------->` block in `printer.cfg`. PID tuning, probe Z offset, and temperature-specific bed meshes are intentionally persisted there.
- `BED_MESH_LOAD` expects saved profiles named `MESH-<bed_temp>C`, and `bedmesh_renew` regenerates that family of profiles across multiple bed temperatures before calling `SAVE_CONFIG`.
- The manual probe workflow is enforced in macros, not just documented: `probeon` / `probeoff` toggle `bedmesh.probe_installed`, `bedmesh` refuses to run unless the probe is marked installed, and `START_PRINT` cancels if the probe is still marked present. Preserve that safety chain when editing start-of-print logic.
- Tool-change behavior is tightly coupled across `PARK_extruder*`, `T0`, `T1`, `set_stepper_x_range`, `clear_stepper_x_range`, `x_xoffset`, and `prepare_toolheads`. The helper macros exist because Klipper/Jinja macro expansion happens before later state changes; avoid collapsing them into a single macro without accounting for that timing.
- `kiauh_macros.cfg` is KIAUH-generated compatibility glue for Mainsail/Fluidd (`CANCEL_PRINT`, `PAUSE`, `RESUME`). Treat it as integration code and preserve the rename/forwarding pattern if those commands are changed.
- `variables.cfg` is not just static configuration: macros such as `INITIALIZE_VARIABLE` and `SAVE_IF_SET` depend on persisted values surviving restarts.
- When switching hardware profiles, keep `printer.cfg` include lines and the matching extruder snippet aligned instead of copying tuning values piecemeal between `extruders_std/` and `extruders_titan/`.
- When the imported Tenlog values disagree with observed Hellbot Hidra Plus behavior, prefer changing the hardware-specific layers (`printer.cfg` motion/pin/probe sections and the selected extruder profile) instead of rewriting unrelated macro flow.
