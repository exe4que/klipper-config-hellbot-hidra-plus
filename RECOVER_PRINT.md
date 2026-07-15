# RECOVER_PRINT

Macro y script para reanudar una impresion en Klipper despues de un corte de energia ingresando solo la altura **Z** actual del nozzle.

## Componentes

- `RECOVER_PRINT`: macro de Klipper que reanuda la impresion.
- `generate_recover_print.py`: script que analiza un `.gcode` y genera el perfil que la macro necesita.

La macro por si sola **no puede** leer la lista de G-codes de Mainsail/Moonraker ni parsear cualquier archivo `.gcode`. Por eso el flujo correcto es:

1. Generar un perfil con el script.
2. Hacer `RESTART` en Klipper.
3. Ejecutar `RECOVER_PRINT Z=<altura>`.

## Ubicacion esperada

- Script: `~/klipper/config/generate_recover_print.py`
- Config generado: `~/klipper/config/recover_data/recover_print_generated.cfg`
- G-codes de Klipper: `~/printer_data/gcodes/`

`printer.cfg` ya debe incluir:

```ini
[include /home/exe4que/klipper/config/recover_data/recover_print_generated.cfg]
```

## Modo automatico

Este modo intenta elegir desde Moonraker el trabajo mas reciente con prioridad:

1. `klippy_shutdown`
2. `interrupted`
3. `klippy_disconnect`
4. `error`
5. `in_progress`

Comando:

```bash
python3 ~/klipper/config/generate_recover_print.py --last-shutdown
```

## Modo manual

Si Moonraker no tiene el archivo correcto, o si queres elegirlo vos, copiá o dejá el `.gcode` dentro de `~/printer_data/gcodes/` y ejecutá:

```bash
python3 ~/klipper/config/generate_recover_print.py ~/printer_data/gcodes/mi_archivo.gcode
```

Tambien podés pasar una ruta relativa a `~/printer_data/gcodes/`.

## Aplicar el perfil generado

Despues de generar el archivo:

```gcode
RESTART
```

Luego reanuda con:

```gcode
RECOVER_PRINT Z=4.37
```

## Comportamiento de la recuperacion

La macro:

- toma la capa cuya Z sea **igual o inmediatamente inferior** a la Z ingresada
- calienta cama y nozzle segun el estado detectado para esa capa
- hace home solo de **X** e **Y**
- **no** agrega un `z-hop` extra si tu `homing_override` ya lo hace
- recompone la posicion virtual de Z
- mueve el nozzle al destino **X/Y** de reentrada mientras sigue arriba
- vuelve a la capa correcta
- reanuda el archivo con `M23` + `M26` + `M24`

## Notas importantes

- El `.gcode` debe existir dentro de `~/printer_data/gcodes/` para que Klipper pueda reanudarlo con `M23`.
- Si cambiás el archivo `.gcode`, tenés que volver a ejecutar el script para regenerar el perfil.
- Si `RECOVER_PRINT` dice que no hay perfil cargado, primero ejecutá el script y despues `RESTART`.
- Si el archivo fue modificado despues del corte, regenerá el perfil contra la version exacta que querés reanudar.

## Ejemplo completo

```bash
python3 ~/klipper/config/generate_recover_print.py --last-shutdown
```

```gcode
RESTART
RECOVER_PRINT Z=5.12
```
