# OGB Console Commands

Reference for every command available in the OpenGrowBox console.

Commands are dispatched per room over the Home Assistant event bus:

| Event | Direction | Payload |
|-------|-----------|---------|
| `ogb_console_command` | in | `{"room": "<room>", "command": "<command line>"}` |
| `ogb_console_response` | out | `{"room": "<room>", "message": "...", "timestamp": "..."}` |
| `ogb_get_commands` | in | `{"room": "<room>", "request_id": "..."}` |
| `ogb_commands_response` | out | `{"room": "<room>", "commands": {...}, "request_id": "..."}` |

A command is only executed by the console whose `room` matches the event.

```bash
# Fire a command
event: ogb_console_command
data:
  room: dev_room
  command: buffer
```

General behaviour:

- `help` lists all commands, `help <command>` or `<command> -h` shows details for one
- Unknown commands and invalid arguments return an `⚠️` message instead of raising
- Commands are **not** available for the ambient room (the console manager is
  skipped there)

---

## Command Overview

| Command | Description | Usage |
|---------|-------------|-------|
**General** |||
| `help` | Shows available commands or details about a command | `help [command]` |
| `version` | Shows the console version | `version` |
| `test` | Executes a test command | `test` |
| `list` | Lists available capabilities or devices | `list [caps\|devices]` |
| `device_states` | Shows current device states for debugging | `device_states` |
| `get_tentdata` | Shows current `tentData` and VPD values | `get_tentdata` |
**Control Tuning** |||
| `gcd` | Sets or shows the global cooldown for a device capability | `gcd <capability> <minutes>` |
| `buffer` | Shows or sets hysteresis buffers | `buffer [damper <type> <value> \| perfection <temp\|humidity> <value>]` |
**Capability Calibration** |||
| `cap_calibrate` | Starts capability calibration | `cap_calibrate <capability>` |
| `cap_cal_status` | Shows capability calibration status and stored results | `cap_cal_status` |
| `calibrate_all_caps` | Calibrates all capabilities in order | `calibrate_all_caps` |
| `cap_cal_stop` | Stops an ongoing capability calibration | `cap_cal_stop` |
**Crop Steering** |||
| `cs_calibrate` | Starts VWC calibration for CropSteering | `cs_calibrate <max\|min\|stop> [phase]` |
| `cs_auto_calib` | Enables/disables automatic VWC calibration | `cs_auto_calib [enable\|disable\|status]` |
| `cs_status` | Shows CropSteering status and calibration values | `cs_status` |
| `cs_soak` | Arms/disarms/shows the one-shot Initial Soak | `cs_soak [on\|off\|status]` |
| `cs_p2` | Controls P2 introduction | `cs_p2 [status\|mode <auto\|enabled\|disabled>\|threshold <n>\|reset]` |
**Script Mode** |||
| `script` | Script mode management | `script <status\|save\|load\|template\|backup\|restore\|validate>` |
**Grow Plan** |||
| `growplan_stop` | Deactivates the active grow plan and restores `tentData` | `growplan_stop` |
| `growplan_pause` | Pauses the active grow plan (frozen, resume to continue) | `growplan_pause` |
| `growplan_resume` | Resumes the most recently active grow plan | `growplan_resume` |
| `get_week` | Shows current grow plan week data | `get_week` |
**Medium** |||
| `medium_sensors` | Shows current grow medium sensor readings | `medium_sensors [medium_name]` |
**Costs / Energy** |||
| `get_costs` | Shows energy consumption and costs | `get_costs` |
| `reset_costs` | Resets energy consumption data | `reset_costs [today\|week\|month\|all]` |

---

## General

### `help`

```bash
help            # list every command
help gcd        # details for one command
gcd -h          # identical
```

### `version`

Prints the console version.

### `test`

Executes a test command. Useful to verify that the console is wired up.

### `list`

```bash
list caps       # or list capabilities - active/inactive capabilities with devices
list devices    # known devices
```

Only capabilities with `state=True` and `count > 0` count as active.

### `device_states`

Dumps the current state of every device, for debugging.

### `get_tentdata`

Shows the current `tentData` values and the computed VPD.

---

## Control Tuning

### `gcd` — Global Cooldown

Per-capability cooldown: how long a device must wait before it may be
re-triggered. Stored under `deviceCooldowns`.

```bash
gcd                    # show all cooldowns
gcd canLight 5         # set light cooldown to 5 minutes
gcd canCO2 2           # set CO2 cooldown to 2 minutes
```

Valid capabilities: `canHumidify`, `canDehumidify`, `canHeat`, `canCool`,
`canExhaust`, `canIntake`, `canVentilate`, `canWindow`, `canDoor`, `canLight`,
`canCO2`, `canClimate`.

Values are sent through the `AdjustDeviceGCD` event, so the cooldown manager
applies and persists them.

### `buffer` — Hysteresis Buffers

Inspect and change the hysteresis buffers, in both buffer groups.

```bash
buffer                        # list all buffers with current values
buffer -h                     # usage

buffer damper cooler 1.5      # dampening buffer for the cooler
buffer damper heater 2.5      # dampening buffer for the heater
buffer damper humidifier 6    # dampening buffer for the humidifier
buffer damper dehumidifier 6  # dampening buffer for the dehumidifier

buffer perfection temp 2.0      # VPD Perfection temperature bounds buffer
buffer perfection humidity 4.0  # VPD Perfection humidity bounds buffer
```

`<channel>` also accepts the aliases `temperature` for `temp` and `hum` for
`humidity`. Input is case-insensitive.

**Dampening buffers** (`controlOptionData.buffers`) prevent a device from
*starting* too close to the opposite limit:

| Key | Device | Default |
|-----|--------|---------|
| `heaterBuffer` | Heater (°C) | `2.0` |
| `coolerBuffer` | Cooler (°C) | `2.0` |
| `humidifierBuffer` | Humidifier (%RH) | `5.0` |
| `dehumidifierBuffer` | Dehumidifier (%RH) | `5.0` |

**VPD Perfection bounds buffers** decide when a correction is injected before a
physical limit is reached:

| Key | Applies to | Default |
|-----|-----------|---------|
| `vpdPerfectionTempBuffer` | Temperature bounds (°C) | `1.5` |
| `vpdPerfectionHumBuffer` | Humidity bounds (%RH) | `3.0` |

```text
$ buffer damper cooler 1.5
✅ Buffer 'coolerBuffer' (damper cooler) set: 2.0 → 1.5

$ buffer damper bogus 1
⚠️ Unknown type: 'bogus'
Available: heater, cooler, humidifier, dehumidifier

$ buffer damper cooler -1
⚠️ Buffer value must be zero or positive.
```

Both reader stages (`OGBDampeningActions`, `OGBVPDActions`) fetch the values
from the datastore on every control cycle, so changes apply immediately — no
restart and no reload. Defaults live once in `DEFAULT_BUFFERS` (`OGBParams.py`).

See also [Configuration → Hysteresis Buffers](configuration/CONFIGURATION.md#hysteresis-buffers).

---

## Capability Calibration

### `cap_calibrate`

Starts a calibration run for a single capability so OpenGrowBox learns the real
response of that device type.

```bash
cap_calibrate canHeat
cap_calibrate canCO2
```

### `cap_cal_status`

Shows the calibration state and stored results of every capability.

### `calibrate_all_caps`

Runs all capabilities through calibration in order:

```text
canHumidify → canDehumidify → canHeat → canCool → canLight
```

### `cap_cal_stop`

Aborts an ongoing calibration run.

---

## Crop Steering

### `cs_calibrate`

```bash
cs_calibrate max        # start max calibration
cs_calibrate max p1     # for phase p1
cs_calibrate min p2     # min calibration for phase p2
cs_calibrate stop       # stop
```

### `cs_auto_calib`

Automatic VWC calibration in Manual-Transition mode (default off).

```bash
cs_auto_calib status
cs_auto_calib enable
cs_auto_calib disable
```

### `cs_status`

Shows CropSteering status and calibration values.

### `cs_soak`

Arms the one-shot Initial Soak. Works in **any** week, so a full pre-soak can be
triggered later.

```bash
cs_soak on
cs_soak off
cs_soak status
```

### `cs_p2`

Controls the P2 introduction.

```bash
cs_p2                       # status
cs_p2 mode auto             # auto | enabled | disabled
cs_p2 threshold 25          # week threshold
cs_p2 reset                 # reset p2_introduced
```

See [Crop Steering](specialized_systems/CROP_STEERING.md) for background.

---

## Script Mode

### `script`

All subcommands act on the **current room's** script, so most take no name.

```bash
script status                  # show the active script
script template basic_vpd_control   # load a template and persist it for this room
script load                    # force reload the script from file
script backup                  # show whether a backup exists
script restore                 # restore the backup
script validate                # syntax/summary check of the active script
script save                    # informational: scripts persist automatically
```

Available templates: `basic_vpd_control`, `advanced_environment`.

`script template <name>` is the command that actually writes the room script —
Script Mode picks it up on the next cycle. A backup is created automatically when
saving.

See [Script Mode](SCRIPT_MODE.md).

---

## Grow Plan

### `growplan_stop`

Deactivates the active grow plan and restores the `tentData` defaults.

### `growplan_pause`

Freezes the active grow plan. Use `growplan_resume` to continue from the same
point.

### `growplan_resume`

Resumes the most recently active grow plan.

### `get_week`

Shows the current grow plan week data.

---

## Medium

### `medium_sensors`

```bash
medium_sensors           # all mediums
medium_sensors coco_1    # one medium
```

Shows current grow medium sensor readings. Requires a VWC-capable medium device.

---

## Costs / Energy

### `get_costs`

Shows energy consumption and costs for today, this week and this month.

### `reset_costs`

```bash
reset_costs today
reset_costs week
reset_costs month
reset_costs all
```

Resets the stored consumption/cost history for the selected range.