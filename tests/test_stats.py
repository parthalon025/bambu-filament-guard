"""The estimators learning uses, against known values (guard_stats)."""
import random

import pytest

import guard_stats as gs


def test_the_conformal_bound_needs_nine_scores_and_is_then_the_largest():
    """ceil((n + 1) x 0.9)-th smallest: none below 9 scores, the largest of 9, the 18th of 19."""
    assert gs.conformal_bound(list(range(8)), 0.1) is None and gs.conformal_bound(list(range(9)), 0.1) == 8 and gs.conformal_bound(list(range(19)), 0.1) == 17

def test_shrinkage_pools_trays_that_cannot_be_told_apart_and_not_those_that_can():
    alike = [gs.group_stats([1.0, 1.02, 0.98], [1, 1, 1]), gs.group_stats([1.01, 0.99, 1.0], [1, 1, 1])]
    apart = [gs.group_stats([1.0, 1.01, 0.99], [1, 1, 1]), gs.group_stats([0.95, 0.96, 0.94], [1, 1, 1]), gs.group_stats([0.97, 0.98, 0.96], [1, 1, 1])]
    assert gs.shrinkage(alike) is None and gs.shrinkage(alike[:1]) is None                   # alike, or one tray: each takes the printer's figure
    assert gs.shrinkage(apart) == 1.0                                                         # far apart: its own prints count, never less than one print's worth
    near = [gs.group_stats([m, m + 0.03, m - 0.03], [1, 1, 1]) for m in (1.0, 0.98, 1.03)]
    means = (1.0, 0.98, 1.03)
    spread = sum((m - sum(means) / 3) ** 2 for m in means) / 2                                # by hand: the variance within is 0.0009; between the true
    assert gs.shrinkage(near) == pytest.approx(0.0009 / (spread - 0.0009 / 3), rel=1e-6) and gs.shrinkage(near) > 1   # means, what the means spread beyond it
    assert gs.group_stats([2.0, 4.0], [1.0, 3.0])[:2] == (3.5, pytest.approx(1.6))            # weighted mean, Kish's effective count

def test_t_quantiles_match_the_tables():
    for df, want in ((1, 6.314), (2, 2.920), (3, 2.353), (5, 2.015), (10, 1.812), (30, 1.697)): assert gs.t_quantile(0.95, df) == pytest.approx(want, abs=0.01)
    assert gs.t_quantile(0.90, 2) == pytest.approx(1.886, abs=0.001) and gs.t_quantile(0.90, 10) == pytest.approx(1.372, abs=0.01)

def test_the_rate_interval_is_fiellers_with_the_slicers_grams_exact():
    pairs = [(100, 98), (50, 49), (200, 197), (30, 29)]
    r = 373 / 380
    half = gs.t_quantile(0.95, 3) * (4 / 3 * sum((q - r * p) ** 2 for p, q in pairs)) ** 0.5 / 380
    assert gs.ratio_interval(pairs) == pytest.approx((r - half, r + half))                    # x without noise: Fieller is the ratio's own t interval
    exact, noisy = gs.fieller(10.0, 9.0, 0.0, 1.0, 0.0, 2.0), gs.fieller(10.0, 9.0, 4.0, 1.0, 0.0, 2.0)
    assert noisy[0] < exact[0] < 0.9 < exact[1] < noisy[1]                                     # a noisy denominator widens it, unevenly
    assert gs.fieller(1.0, 1.0, 1.0, 1.0, 0.0, 2.0) is None and gs.ratio_interval(pairs[:1]) is None

def test_kaplan_meier_on_the_flipped_scale_takes_spools_still_feeding_as_below():
    assert gs.km_upper([(20, True), (5, True), (3, True)], 0.1) == 20                         # fewer than ten: the largest
    assert gs.km_upper([(20, True)] + [(12, False)] * 10, 0.1) is None                        # ten spools fed below 20 without stopping: 20 is the odd one
    assert gs.km_upper([(20, True)] + [(25, False)] * 10, 0.1) == 20                          # spools that left above it say nothing of it
    assert gs.km_upper([(12, False)] * 3, 0.1) is None and gs.km_upper([(10, True), (10, False)], 0.1) == 10   # a tie: the exact value first
    assert gs.km_upper([(v, True) for v in range(1, 12)], 0.1) == 10

def test_the_sign_test():
    assert gs.sign_p(5, 5) == 1 / 32 and gs.sign_p(0, 4) == 1.0 and gs.sign_p(8, 10) == pytest.approx(56 / 1024) and gs.sign_p(0, 0) == 1.0

def test_a_change_point_needs_two_values_that_agree_not_one_odd_one():
    rnd = random.Random(7)
    base, prior = [rnd.gauss(0, 0.01) for _ in range(30)], (0.0, 1.0, 1.0, 0.03 ** 2)
    recent = lambda xs, odd=0.05: sum(gs.run_lengths(xs, 1 / 50, prior, odd)[-1][2:6])
    assert recent(base) < 0.05 and recent(base + [0.08]) < 0.5 and recent(base + [0.08, 0.0, 0.0]) < 0.5
    assert recent(base + [0.08, 0.0, 0.0], odd=0.0) > 0.5                                   # with no odd value allowed for, one ends the run for good
    assert recent(base + [0.08, 0.08]) > 0.5 and recent(base + [-0.045, -0.045]) > 0.5
    assert all(sum(p) == pytest.approx(1.0) for p in gs.run_lengths(base + [0.08, 0.08], 1 / 50, prior, 0.05))

def test_the_spread_from_spool_to_spool_bounds_one_more_spool():
    assert gs.pooled_spread([[20.0, 22.0, 18.0], [5.0]]) == (2.0, 2) and gs.pooled_spread([[1.0], [2.0]]) == (None, 0)
    assert gs.prediction_bound([5.0], 2.0, 2, 0.9) == pytest.approx(5 + 1.8856 * 2 * 2 ** 0.5, abs=1e-3)
    assert gs.quantile([1, 2, 3, 4], 0.5) == 2.5 and gs.quantile([], 0.5) is None
