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
    int target = -1;
    assert(current_duty_step(0, 255, 0, 13000, 13000, 700, &target) > 0);
    assert(target == 12949);
    assert(current_duty_step(50000, 0, -10000, 13000, 13000, 700, &target) == 0);
    assert(target == 0);
    assert(current_duty_step(65535, 255, 0, 100000, 100000, 1, &target) == 65535);
    assert(current_duty_step(100, 255, 1000000, 13000, 13000, 1, &target) == 0);
    puts("Hall calibration, filtering, transition recovery and current control tests passed.");
}
