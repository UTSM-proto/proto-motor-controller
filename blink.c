#include <stdio.h>
#include "hardware/adc.h"
#include "hardware/clocks.h"
#include "hardware/gpio.h"
#include "hardware/irq.h"
#include "hardware/pwm.h"
#include "hardware/sync.h"
#include "pico/stdlib.h"
#include <controller_config.h>
#include "controller_logic.h"

#ifndef PROGRAMMER_BUILD_ID
#define PROGRAMMER_BUILD_ID "manual"
#endif

static uint8_t hallToMotor[8] = HALL_TABLE_INITIALIZER;
static uint phase_slices[3];
static HallFilter hall_filter = {255, 0, 255, false};
static int adc_bias;
static volatile bool armed;
static uint32_t active_cycles;
static volatile int duty_cycle, current_ma, current_target_ma, voltage_mv;
static volatile unsigned hall = 255, motor_state = 255;
static volatile uint32_t invalid_hall_samples, transition_faults, adc_errors;
static volatile unsigned raw_hall, throttle_adc_last, throttle_last;
static volatile unsigned drive_block; // 0 drive, 1 not armed, 2 throttle, 3 halls, 4 transition, 5 settling, 6 ADC
static const char *cal_result = "not_started";
static unsigned cal_sector = 255, cal_code = 255, cal_previous = 255;
static uint8_t cal_observed[6] = {255, 255, 255, 255, 255, 255};

static void write_phases(uint ah, uint bh, uint ch, uint al, uint bl, uint cl) {
    pwm_set_both_levels(phase_slices[0], ah, 255 - al);
    pwm_set_both_levels(phase_slices[1], bh, 255 - bl);
    pwm_set_both_levels(phase_slices[2], ch, 255 - cl);
}
static void write_pwm(unsigned state, unsigned duty, bool synchronous) {
    if (!duty || duty > 255) state = 255;
    if (duty > PWM_FULL_ON_THRESHOLD) duty = 255;
    unsigned complement = synchronous && duty < PWM_COMPLEMENT_TOTAL
        ? PWM_COMPLEMENT_TOTAL - duty : 0;
    switch (state) {
        case 0: write_phases(0, duty, 0, 255, complement, 0); break;
        case 1: write_phases(0, 0, duty, 255, 0, complement); break;
        case 2: write_phases(0, 0, duty, 0, 255, complement); break;
        case 3: write_phases(duty, 0, 0, complement, 255, 0); break;
        case 4: write_phases(duty, 0, 0, complement, 0, 255); break;
        case 5: write_phases(0, duty, 0, 0, complement, 255); break;
        default: write_phases(0, 0, 0, 0, 0, 0); break;
    }
}
static unsigned read_halls(void) {
    unsigned votes[8] = {0};
    for (unsigned i = 0; i < HALL_OVERSAMPLE; ++i) {
        // One register read captures all three GPIOs at the same instant.
        uint32_t pins = gpio_get_all();
        unsigned code = ((pins >> HALL_1_PIN) & 1u)
            | (((pins >> HALL_2_PIN) & 1u) << 1)
            | (((pins >> HALL_3_PIN) & 1u) << 2);
        raw_hall = code;
        ++votes[code];
    }
    return hall_majority(votes, HALL_OVERSAMPLE);
}
static unsigned read_adc_pin(unsigned pin) {
    adc_select_input(pin - 26);
    return adc_read();
}
static void on_pwm_wrap(void) {
    pwm_clear_irq(phase_slices[0]);
    gpio_put(FLAG_PIN, 1);
    // Fixed-order completed conversions; no FIFO and no stale samples.
    unsigned isense = read_adc_pin(ISENSE_PIN);
    unsigned vsense = read_adc_pin(VSENSE_PIN);
    unsigned throttle_adc = read_adc_pin(THROTTLE_PIN);
    throttle_adc_last = throttle_adc;
    if (adc_hw->cs & ADC_CS_ERR_STICKY_BITS) {
        hw_set_bits(&adc_hw->cs, ADC_CS_ERR_STICKY_BITS);
        ++adc_errors;
        duty_cycle = 0; armed = false; active_cycles = 0;
        drive_block = 6;
        write_pwm(255, 0, false);
        gpio_put(FLAG_PIN, 0);
        return;
    }
    int throttle = ((int)throttle_adc - THROTTLE_LOW) * 256 / (THROTTLE_HIGH - THROTTLE_LOW);
    throttle = throttle < 0 ? 0 : throttle > 255 ? 255 : throttle;
    throttle_last = throttle;
    // Require released throttle after boot or an ADC error before drive.
    if (!throttle) armed = true;
    current_ma = (int)(((int)isense - adc_bias) * (float)CURRENT_SCALING);
    voltage_mv = (int)(vsense * (float)VOLTAGE_SCALING);
    hall = read_halls();
    bool previously_faulted = hall_filter.transition_fault;
    motor_state = hall_filter_step(&hall_filter, hall, hallToMotor,
        HALL_STABLE_CYCLES, HALL_VALIDATE_TRANSITIONS, throttle == 0);
    if (hall == 255) ++invalid_hall_samples;
    if (!previously_faulted && hall_filter.transition_fault) ++transition_faults;
    if (!armed || !throttle || hall == 255 || hall_filter.transition_fault) {
        drive_block = !armed ? 1 : !throttle ? 2 : hall == 255 ? 3 : 4;
        duty_cycle = 0; current_target_ma = 0; active_cycles = 0;
        write_pwm(255, 0, false);
    } else if (motor_state > 5) {
        drive_block = 5;
        // A valid new sector is still settling. Blank this cycle without
        // restarting the throttle ramp on every normal commutation edge.
        write_pwm(255, 0, false);
    } else {
        drive_block = 0;
        if (CURRENT_CONTROL) {
            int target;
            duty_cycle = current_duty_step(duty_cycle, throttle, current_ma,
                PHASE_MAX_CURRENT_MA, BATTERY_MAX_CURRENT_MA,
                CURRENT_CONTROL_LOOP_GAIN, &target);
            current_target_ma = target;
        } else {
            int target = throttle * 256;
            duty_cycle = duty_cycle < target
                ? (duty_cycle + THROTTLE_SLEW_RATE < target
                    ? duty_cycle + THROTTLE_SLEW_RATE : target) : target;
        }
        if (active_cycles < UINT32_MAX) ++active_cycles;
        write_pwm(motor_state, duty_cycle / 256,
            SYNCHRONOUS_SWITCHING && active_cycles > SYNCHRONOUS_DELAY_CYCLES);
    }
    gpio_put(FLAG_PIN, 0);
}
static void energize_half_state(unsigned sector) {
    write_pwm(sector, HALL_IDENTIFY_DUTY_CYCLE, false); sleep_us(500);
    write_pwm((sector + 1) % 6, HALL_IDENTIFY_DUTY_CYCLE, false); sleep_us(500);
}
static bool identify_halls(void) {
    cal_result = "running";
    uint8_t candidate[8];
    memset(candidate, 255, sizeof candidate);
    sleep_ms(BOOT_DELAY_MS);
    for (unsigned sector = 0; sector < 6; ++sector) {
        cal_sector = sector;
        for (unsigned ms = 0; ms < HALL_IDENTIFY_SETTLE_MS; ++ms) energize_half_state(sector);
        unsigned code = 255;
        for (unsigned sample = 0; sample < HALL_IDENTIFY_STABLE_SAMPLES; ++sample) {
            energize_half_state(sector);
            unsigned now = read_halls();
            cal_code = now;
            cal_previous = code;
            cal_observed[sector] = now;
            if (now == 255 || (sample && now != code)) {
                write_pwm(255, 0, false);
                cal_result = now == 255 ? "invalid_hall" : "unstable_hall";
                printf("CALIBRATION FAILED: unstable hall at sector %u\n", sector);
                return false;
            }
            code = now;
        }
        unsigned state = (sector + (IDENTIFY_HALLS_REVERSE ? 5 : 2)) % 6;
        if (!hall_candidate_add(candidate, code, state)) {
            write_pwm(255, 0, false);
            cal_result = "duplicate_hall";
            printf("CALIBRATION FAILED: duplicate hall %u at sector %u\n", code, sector);
            return false;
        }
    }
    write_pwm(255, 0, false);
    if (!hall_table_valid(candidate)) { cal_result = "invalid_table"; return false; }
    // Publish only a complete validated table.
    memcpy(hallToMotor, candidate, sizeof candidate);
    cal_result = "passed";
    return true;
}
static void init_hardware(void) {
    stdio_init_all();
    gpio_init(LED_PIN); gpio_set_dir(LED_PIN, GPIO_OUT);
    gpio_init(FLAG_PIN); gpio_set_dir(FLAG_PIN, GPIO_OUT);
    const uint halls[] = {HALL_1_PIN, HALL_2_PIN, HALL_3_PIN};
    for (unsigned i = 0; i < 3; ++i) {
        gpio_init(halls[i]); gpio_set_dir(halls[i], GPIO_IN);
        gpio_disable_pulls(halls[i]);
        gpio_set_input_hysteresis_enabled(halls[i], true);
    }
    const uint highs[] = {AH_PIN, BH_PIN, CH_PIN};
    const uint lows[] = {AL_PIN, BL_PIN, CL_PIN};
    pwm_config config = pwm_get_default_config();
    pwm_config_set_clkdiv(&config, (float)clock_get_hz(clk_sys) / (F_PWM * 254 * 2));
    pwm_config_set_wrap(&config, 254);
    pwm_config_set_phase_correct(&config, true);
    pwm_config_set_output_polarity(&config, false, true);
    uint32_t mask = 0;
    for (unsigned i = 0; i < 3; ++i) {
        phase_slices[i] = pwm_gpio_to_slice_num(highs[i]);
        pwm_init(phase_slices[i], &config, false);
        // Set off levels AFTER pwm_init clears compare registers and BEFORE
        // muxing the pins. Inverted low outputs otherwise initialize on.
        pwm_set_both_levels(phase_slices[i], 0, 255);
        gpio_set_function(highs[i], GPIO_FUNC_PWM);
        gpio_set_function(lows[i], GPIO_FUNC_PWM);
        mask |= 1u << phase_slices[i];
    }
    pwm_set_mask_enabled(mask);
    adc_init();
    adc_gpio_init(ISENSE_PIN); adc_gpio_init(VSENSE_PIN); adc_gpio_init(THROTTLE_PIN);
    adc_set_round_robin(0);
    sleep_ms(100);
    uint64_t bias_sum = 0;
    for (unsigned i = 0; i < ADC_BIAS_OVERSAMPLE; ++i) bias_sum += read_adc_pin(ISENSE_PIN);
    adc_bias = bias_sum / ADC_BIAS_OVERSAMPLE;
}
int main(void) {
    init_hardware();
    bool valid = IDENTIFY_HALLS_ON_BOOT ? identify_halls() : hall_table_valid(hallToMotor);
    if (!IDENTIFY_HALLS_ON_BOOT) cal_result = valid ? "manual_table" : "invalid_manual_table";
    printf("hallToMotor array:");
    for (unsigned i = 0; i < 8; ++i) printf(" %u", hallToMotor[i]);
    printf("\n");
    if (!valid) {
        write_pwm(255, 0, false);
        while (true) {
            printf("build=%s\n", PROGRAMMER_BUILD_ID);
            printf("FAULT: hall calibration/table invalid; drive disabled. Reconfigure or reboot.\n");
            unsigned live_hall = read_halls();
            printf("diag cal=%s cal_sector=%u cal_code=%u cal_previous=%u raw_hall=%u hall=%u motor=255 duty=0 armed=0 throttle_adc=%u block=calibration_failed\n",
                cal_result, cal_sector, cal_code, cal_previous, raw_hall, live_hall, read_adc_pin(THROTTLE_PIN));
            printf("cal_observed: %u %u %u %u %u %u\n", cal_observed[0], cal_observed[1], cal_observed[2], cal_observed[3], cal_observed[4], cal_observed[5]);
            gpio_put(LED_PIN, !gpio_get(LED_PIN)); sleep_ms(500);
        }
    }
    irq_set_exclusive_handler(PWM_IRQ_WRAP, on_pwm_wrap);
    irq_set_priority(PWM_IRQ_WRAP, 0);
    pwm_clear_irq(phase_slices[0]);
    irq_set_enabled(PWM_IRQ_WRAP, true);
    pwm_set_irq_enabled(phase_slices[0], true);
    while (true) {
        uint32_t flags = save_and_disable_interrupts();
        int current = current_ma, target = current_target_ma, duty = duty_cycle, volts = voltage_mv;
        unsigned h = hall, state = motor_state;
        uint32_t invalid = invalid_hall_samples, transitions = transition_faults, adc = adc_errors;
        unsigned raw = raw_hall, throttle_adc = throttle_adc_last, throttle = throttle_last, block = drive_block;
        bool is_armed = armed;
        restore_interrupts(flags);
        printf("build=%s\n", PROGRAMMER_BUILD_ID);
        const char *blocks[] = {"drive", "throttle_not_released", "zero_throttle", "invalid_hall", "transition_fault", "hall_settling", "adc_error"};
        printf("diag cal=%s throttle_adc=%u throttle=%u armed=%u raw_hall=%u hall=%u motor=%u duty=%d block=%s\n",
            cal_result, throttle_adc, throttle, is_armed, raw, h, state, duty, blocks[block < 7 ? block : 6]);
        printf("hallToMotor array:");
        for (unsigned i = 0; i < 8; ++i) printf(" %u", hallToMotor[i]);
        printf("\n");
        printf("%d,%d,%d,%d,%u,%u,invalid=%lu,transitions=%lu,adc=%lu\n",
            current, target, duty, volts, h, state,
            (unsigned long)invalid, (unsigned long)transitions, (unsigned long)adc);
        gpio_put(LED_PIN, !gpio_get(LED_PIN)); sleep_ms(TELEMETRY_INTERVAL_MS);
    }
}
