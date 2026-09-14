#include <assert.h>
#include <stdio.h>
#include "../controller_logic.h"
int main(void) {
    uint8_t table[8] = {255, 0, 1, 2, 3, 4, 5, 255};
    assert(hall_table_valid(table));
    uint8_t candidate[8]; memset(candidate, 255, 8);
    assert(!hall_candidate_add(candidate, 0, 0));
    assert(!hall_candidate_add(candidate, 7, 0));
    assert(!hall_candidate_add(candidate, 255, 0));
    assert(!hall_table_valid(candidate));
    for (unsigned i = 1; i < 7; ++i) assert(hall_candidate_add(candidate, i, i - 1));
    assert(!hall_candidate_add(candidate, 1, 5));
    assert(hall_table_valid(candidate));
    candidate[2] = 0; assert(!hall_table_valid(candidate));
    unsigned votes[8] = {0}; votes[1] = 4; votes[2] = 4;
    assert(hall_majority(votes, 8) == 255);
    votes[1] = 5; votes[2] = 3; assert(hall_majority(votes, 8) == 1);
    memset(votes, 0, sizeof votes); votes[0] = 8; assert(hall_majority(votes, 8) == 255);
    HallFilter f = {255, 0, 255, false};
    assert(hall_filter_step(&f, 1, table, 2, true, true) == 255);
    assert(hall_filter_step(&f, 1, table, 2, true, true) == 0);
    assert(hall_filter_step(&f, 2, table, 2, true, false) == 255);
    assert(hall_filter_step(&f, 2, table, 2, true, false) == 1);
    assert(hall_filter_step(&f, 255, table, 2, true, false) == 255);
    assert(hall_filter_step(&f, 2, table, 2, true, false) == 255);
    assert(hall_filter_step(&f, 2, table, 2, true, false) == 1);
    // Non-adjacent sector locks out drive until throttle release.
    hall_filter_step(&f, 5, table, 2, true, false);
    assert(hall_filter_step(&f, 5, table, 2, true, false) == 255);
    assert(f.transition_fault);
    assert(hall_filter_step(&f, 5, table, 2, true, true) == 4);
    assert(!f.transition_fault);
    // Reverse transitions and wrap-around are valid.
    assert(hall_filter_step(&f, 4, table, 1, true, false) == 3);
    f.accepted = 0; assert(hall_filter_step(&f, 6, table, 1, true, false) == 5);
    // Repeated isolated ambiguous samples must blank outputs, never restart
    // the ramp. Exercise the actual runtime filter + drive guard together.
    HallDriveGuard guard = {0, false};
    f = (HallFilter){255, 0, 255, false};
    int duty = 39628;
    unsigned state;
    for (unsigned event = 0; event < 13; ++event) {
        state = hall_filter_step(&f, 255, table, 2, true, false);
        assert(hall_drive_step(&guard, state, f.transition_fault, true, 185,
                              16, false, &duty) == HALL_DRIVE_BLANK);
        assert(duty == 39628);
        state = hall_filter_step(&f, 1, table, 2, true, false);
        assert(hall_drive_step(&guard, state, f.transition_fault, true, 185,
                              16, false, &duty) == HALL_DRIVE_BLANK);
        state = hall_filter_step(&f, 1, table, 2, true, false);
        assert(hall_drive_step(&guard, state, f.transition_fault, true, 185,
                              16, false, &duty) == HALL_DRIVE_RUN);
        assert(duty == 39628 && guard.uncertain_cycles == 0);
    }
    // Falling demand is honored during a gap; current-mode integral freezes.
    assert(hall_drive_step(&guard, 255, false, true, 10, 16, false, &duty)
           == HALL_DRIVE_BLANK);
    assert(duty == 2560);
    assert(hall_drive_step(&guard, 255, false, true, 1, 16, true, &duty)
           == HALL_DRIVE_BLANK);
    assert(duty == 2560);
    // Sustained loss, including constantly unsettled valid inputs, latches.
    guard = (HallDriveGuard){0, false};
    for (unsigned cycle = 1; cycle <= 16; ++cycle) {
        state = hall_filter_step(&f, cycle % 2 + 1, table, 2, true, false);
        assert(state == 255);
        HallDriveAction action = hall_drive_step(&guard, state, false, true,
                                                 185, 16, false, &duty);
        assert(action == (cycle < 16 ? HALL_DRIVE_BLANK : HALL_DRIVE_RESET));
    }
    assert(guard.loss_fault && duty == 0);
    assert(hall_drive_step(&guard, 0, false, true, 185, 16, false, &duty)
           == HALL_DRIVE_RESET); // good feedback alone cannot restart
    assert(hall_drive_step(&guard, 0, false, true, 0, 16, false, &duty)
           == HALL_DRIVE_RESET);
    assert(!guard.loss_fault && duty == 0);
    assert(hall_drive_step(&guard, 0, false, true, 185, 16, false, &duty)
           == HALL_DRIVE_RUN);
    // Release, unarmed state and skipped sectors still reset immediately.
    duty = 40000;
    assert(hall_drive_step(&guard, 255, false, true, 0, 16, false, &duty)
           == HALL_DRIVE_RESET && duty == 0);
    duty = 40000;
    assert(hall_drive_step(&guard, 0, false, false, 185, 16, false, &duty)
           == HALL_DRIVE_RESET && duty == 0);
    duty = 40000;
    assert(hall_drive_step(&guard, 0, true, true, 185, 16, false, &duty)
           == HALL_DRIVE_RESET && duty == 0);
    int target = -1;
    assert(current_duty_step(0, 255, 0, 13000, 13000, 700, &target) > 0);
    assert(target == 12949);
    assert(current_duty_step(50000, 0, -10000, 13000, 13000, 700, &target) == 0);
    assert(target == 0);
    assert(current_duty_step(65535, 255, 0, 100000, 100000, 1, &target) == 65535);
    assert(current_duty_step(100, 255, 1000000, 13000, 13000, 1, &target) == 0);
    puts("Hall calibration, filtering, transition recovery and current control tests passed.");
}
