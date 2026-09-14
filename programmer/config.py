"""Single source of truth for the Windows editor and generated firmware header."""
import math


def field(key, label, group, default, low, high, help, step=1):
    return dict(key=key, label=label, group=group, default=default, min=low,
                max=high, step=step, help=help, type="bool" if isinstance(default, bool) else "number")


FIELDS = [
    field("THROTTLE_LOW", "Throttle starts at (ADC counts)", "Throttle", 540, 0, 4094, "Demand is zero at or below this reading. Voltage uses your external input scale, not the Pico pin voltage."),
    field("THROTTLE_HIGH", "Full throttle (ADC counts)", "Throttle", 3300, 1, 4095, "Demand reaches full scale here. Must exceed the start threshold."),
    field("THROTTLE_INPUT_FULL_SCALE_V", "External throttle full scale (V)", "Throttle", 5.0, 0.1, 100, "Display conversion only: 4095 counts equals this external voltage. This does not make a Pico ADC 5 V tolerant.", 0.01),
    field("THROTTLE_SLEW_RATE", "Throttle rise per control cycle", "Throttle", 1, 1, 65535, "Internal duty counts added each PWM cycle. Full-scale rise time is approximately 65535 / (rate × PWM Hz) seconds. Decreases apply immediately."),
    field("CURRENT_CONTROL", "Control mode: current control", "Current & switching", False, 0, 1, "Off uses throttle-to-duty control. On uses measured current feedback; requires verified sensor polarity and calibration."),
    field("PHASE_MAX_CURRENT_MA", "Phase current target limit (mA)", "Current & switching", 13000, 1, 100000, "Maximum demanded phase current in current-control mode. This is not an independent hardware overcurrent trip."),
    field("BATTERY_MAX_CURRENT_MA", "Battery current target limit (mA)", "Current & switching", 13000, 1, 100000, "Limits the current target using duty ratio in current-control mode."),
    field("CURRENT_CONTROL_LOOP_GAIN", "Current loop divisor", "Current & switching", 700, 1, 100000, "Current error is divided by this value before changing duty. Larger values make the loop respond more slowly."),
    field("SYNCHRONOUS_SWITCHING", "Synchronous switching / regen", "Current & switching", False, 0, 1, "Enables complementary switching and can cause strong regenerative braking as demand decreases. Default remains off in both modes."),
    field("SYNCHRONOUS_DELAY_CYCLES", "Synchronous startup delay (cycles)", "Current & switching", 16000, 0, 1000000, "Control cycles before complementary switching is permitted."),
    field("IDENTIFY_HALLS_ON_BOOT", "Automatically identify halls on boot", "Hall calibration", True, 0, 1, "Moves the motor once at startup. Six unique stable valid hall states are required; failure keeps the phases off. Off requires a valid manual table."),
    field("IDENTIFY_HALLS_REVERSE", "Reverse identification direction", "Hall calibration", True, 0, 1, "Selects the commutation offset used during automatic identification. Does not change an existing manual table."),
    field("HALL_IDENTIFY_DUTY_CYCLE", "Identification duty (0–255)", "Hall calibration", 25, 1, 100, "Torque applied during startup calibration. Too little can fail to overcome cogging or load. Increase gradually."),
    field("HALL_IDENTIFY_SETTLE_MS", "Identification settling per sector (ms)", "Hall calibration", 500, 100, 5000, "Time allowed for the rotor to settle into each of six electrical sectors."),
    field("HALL_IDENTIFY_STABLE_SAMPLES", "Stable calibration samples", "Hall calibration", 20, 2, 100, "Consecutive identical hall codes required at each sector, sampled at 1 ms intervals while the sector remains energized."),
    field("BOOT_DELAY_MS", "Delay before calibration (ms)", "Hall calibration", 2000, 0, 10000, "Time after initialization before automatic motor movement."),
    field("HALL_OVERSAMPLE", "Whole-state samples per read", "Hall filtering", 8, 1, 32, "Votes on complete three-bit GPIO snapshots. Ties and invalid codes are rejected; this avoids manufacturing a state by voting on individual bits."),
    field("HALL_STABLE_CYCLES", "Runtime stable cycles", "Hall filtering", 2, 1, 16, "Consecutive control cycles required to accept a hall state. Pending or invalid states turn phases off. Excess filtering limits maximum electrical speed."),
    field("HALL_LOSS_TIMEOUT_CYCLES", "Hall loss timeout (cycles)", "Hall filtering", 16, 2, 320, "Outputs turn off immediately for uncertain Hall feedback. Brief gaps preserve the throttle ramp. This many consecutive cycles without an accepted sector latch drive off until throttle release. Default 16 cycles is 1 ms at 16 kHz; must exceed runtime stable cycles."),
    field("HALL_VALIDATE_TRANSITIONS", "Reject skipped hall sectors", "Hall filtering", True, 0, 1, "Accepts only adjacent commutation sectors in either direction while driving. Release throttle to resynchronize after a skipped-sector fault."),
    field("CURRENT_SCALING", "Current scale (mA / ADC count)", "Sensors & timing", 80.56640625, 0.001, 10000, "Multiplies zero-corrected current ADC counts. Original circuit: 3.3 V reference, 0.5 mΩ shunt, gain 20.", 0.001),
    field("VOLTAGE_SCALING", "Bus scale (mV / ADC count)", "Sensors & timing", 18.017578125, 0.001, 10000, "Converts bus-voltage ADC counts using the 47 kΩ / 2.2 kΩ divider. Verify against a meter.", 0.001),
    field("ADC_BIAS_OVERSAMPLE", "Current zero samples at boot", "Sensors & timing", 1000, 1, 10000, "Averages the current-sensor zero with phases off before calibration."),
    field("F_PWM", "PWM / control frequency (Hz)", "Sensors & timing", 16000, 2000, 20000, "Changes switching losses, audible noise, control cadence and slew time. Restricted to keep ordered ADC conversions inside a cycle."),
    field("TELEMETRY_INTERVAL_MS", "USB telemetry interval (ms)", "Sensors & timing", 100, 20, 5000, "How often firmware prints readings, hall state and diagnostic counters."),
    field("PWM_FULL_ON_THRESHOLD", "Full-on threshold (0–255)", "Advanced PWM", 245, 1, 254, "Duty above this becomes continuously on. Preserves the existing gate-driver bootstrap behavior; requires bench validation."),
    field("PWM_COMPLEMENT_TOTAL", "Complementary duty total", "Advanced PWM", 248, 1, 254, "Complement is max(0, total − duty). Lower values add more blanking when synchronous switching is enabled."),
]

for key, pin, help in [
    ("LED_PIN", 25, "Status LED GPIO."), ("FLAG_PIN", 2, "Oscilloscope timing output GPIO."),
    ("AH_PIN", 16, "Phase A high gate GPIO; even pin, followed by A low."),
    ("AL_PIN", 17, "Phase A low gate GPIO; must follow A high."),
    ("BH_PIN", 18, "Phase B high gate GPIO; even pin, followed by B low."),
    ("BL_PIN", 19, "Phase B low gate GPIO; must follow B high."),
    ("CH_PIN", 20, "Phase C high gate GPIO; even pin, followed by C low."),
    ("CL_PIN", 21, "Phase C low gate GPIO; must follow C high."),
    ("HALL_1_PIN", 13, "Hall A input GPIO, bit 0 of the hall code."),
    ("HALL_2_PIN", 14, "Hall B input GPIO, bit 1 of the hall code."),
    ("HALL_3_PIN", 15, "Hall C input GPIO, bit 2 of the hall code."),
    ("ISENSE_PIN", 26, "Current ADC GPIO (26–28)."),
    ("VSENSE_PIN", 27, "Bus voltage ADC GPIO (26–28)."),
    ("THROTTLE_PIN", 28, "Throttle ADC GPIO (26–28)."),
]:
    FIELDS.append(field(key, key, "Board pins", pin, 26 if key in ("ISENSE_PIN", "VSENSE_PIN", "THROTTLE_PIN") else 0, 28, help))


def defaults():
    return {**{f["key"]: f["default"] for f in FIELDS}, "HALL_TABLE": [255] * 8}


def validate(values):
    if not isinstance(values, dict) or set(values) != set(defaults()):
        raise ValueError("Configuration fields must match this programmer version. Reset defaults or import a current profile.")
    for f in FIELDS:
        v = values[f["key"]]
        if f["type"] == "bool":
            if type(v) is not bool:
                raise ValueError(f'{f["key"]} must be on or off.')
        elif type(v) not in (int, float) or not math.isfinite(v) or not f["min"] <= v <= f["max"] or (f["step"] == 1 and v != int(v)):
            raise ValueError(f'{f["key"]} must be between {f["min"]} and {f["max"]}' + (" and a whole number." if f["step"] == 1 else "."))
    if values["THROTTLE_LOW"] >= values["THROTTLE_HIGH"]:
        raise ValueError("Throttle start must be below full throttle.")
    if values["HALL_LOSS_TIMEOUT_CYCLES"] <= values["HALL_STABLE_CYCLES"]:
        raise ValueError("Hall loss timeout must exceed runtime stable cycles.")
    pins = [values[f["key"]] for f in FIELDS if f["group"] == "Board pins"]
    if len(set(pins)) != len(pins):
        raise ValueError("GPIO assignments must be unique.")
    if any(pin in (23, 24) for pin in pins):
        raise ValueError("GPIO23 and GPIO24 are reserved by the Pico board and are not exposed header pins.")
    slices = []
    for phase in "ABC":
        hi, lo = values[phase + "H_PIN"], values[phase + "L_PIN"]
        if hi % 2 or lo != hi + 1:
            raise ValueError("Each phase needs an even high GPIO followed by its odd low GPIO.")
        slices.append((int(hi) // 2) % 8)
    if len(set(slices)) != 3:
        raise ValueError("Each phase must use a different PWM slice.")
    table = values["HALL_TABLE"]
    if not isinstance(table, list) or len(table) != 8 or any(type(v) is not int or v not in [0, 1, 2, 3, 4, 5, 255] for v in table) or table[0] != 255 or table[7] != 255:
        raise ValueError("Hall table needs eight values, with 255 at positions 0 and 7.")
    if not values["IDENTIFY_HALLS_ON_BOOT"] and sorted(table[1:7]) != list(range(6)):
        raise ValueError("Manual hall table must contain each motor state 0–5 exactly once.")
    return values


def header(values):
    validate(values)
    lines = ["// Generated configuration. Edit with the Windows programmer.", "#pragma once"]
    for f in FIELDS:
        v = values[f["key"]]
        literal = str(int(v)) if f["type"] == "bool" or f["step"] == 1 else format(v, ".12g")
        lines.append(f'#define {f["key"]} ({literal})')
    lines += ["#define HALL_TABLE_INITIALIZER {" + ", ".join(map(str, values["HALL_TABLE"])) + "}",
              "#define DUTY_CYCLE_MAX 65535", "// PWM slices and ADC channels are derived from GPIO assignments."]
    return "\n".join(lines) + "\n"
