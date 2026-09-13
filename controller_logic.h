#pragma once
#include <stdbool.h>
#include <stdint.h>
#include <string.h>

static inline bool hall_table_valid(const uint8_t table[8]) {
    unsigned seen = 0;
    if (table[0] != 255 || table[7] != 255) return false;
    for (unsigned i = 1; i < 7; ++i) {
        if (table[i] > 5 || (seen & (1u << table[i]))) return false;
        seen |= 1u << table[i];
    }
    return seen == 63;
}
static inline bool hall_candidate_add(uint8_t table[8], unsigned code, unsigned state) {
    if (code < 1 || code > 6 || state > 5 || table[code] != 255) return false;
    table[code] = (uint8_t)state;
    return true;
}
static inline unsigned hall_majority(const unsigned votes[8], unsigned count) {
    for (unsigned code = 1; code < 7; ++code)
        if (votes[code] > count / 2) return code;
    return 255;
}
typedef struct {
    unsigned candidate, consecutive, accepted;
    bool transition_fault;
} HallFilter;
// Invalid input turns phases off. Skipped sectors require throttle release.
static inline unsigned hall_filter_step(HallFilter *f, unsigned raw,
        const uint8_t table[8], unsigned stable_cycles, bool check, bool released) {
    if (released) { f->transition_fault = false; f->accepted = 255; }
    if (raw < 1 || raw > 6 || table[raw] > 5) {
        f->candidate = 255; f->consecutive = 0; return 255;
    }
    if (f->candidate != raw) { f->candidate = raw; f->consecutive = 1; }
    else if (f->consecutive < stable_cycles) ++f->consecutive;
    if (f->consecutive < stable_cycles || f->transition_fault) return 255;
    unsigned state = table[raw];
    if (check && f->accepted < 6 && state != f->accepted) {
        unsigned delta = (state + 6 - f->accepted) % 6;
        if (delta != 1 && delta != 5) { f->transition_fault = true; return 255; }
    }
    f->accepted = state;
    return state;
}
static inline int clamp_duty(int64_t duty) {
    return duty < 0 ? 0 : duty > 65535 ? 65535 : (int)duty;
}
static inline int current_duty_step(int duty, int throttle, int current_ma,
        int phase_max, int battery_max, int gain, int *target) {
    if (!throttle) { *target = 0; return 0; }
    int64_t demand = (int64_t)throttle * phase_max / 256;
    int64_t limit = (int64_t)battery_max * 65535 / (duty > 0 ? duty : 1);
    *target = (int)(demand < limit ? demand : limit);
    return clamp_duty((int64_t)duty + ((int64_t)*target - current_ma) / gain);
}
