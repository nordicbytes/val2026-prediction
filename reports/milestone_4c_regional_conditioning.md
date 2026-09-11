# Milestone 4C — Regional conditioning

## Status

Milestone 4B remains byte-for-byte frozen as **SUPPORTED**. Its report SHA-256
is `16da240fe08876646fd97a60c53f5b01ac7d87e042b3b492051324a17533e7f7`. This is a separate 4C result and does not revise
4A or 4B.

Verdict: **NOT_SUPPORTED**

## What 4C can test

Public PSU data has no `previous_party × current_party × region` table. 4C
therefore does not estimate an observed three-way joint. It keeps the locked
national 4B kernel `T1_no_point_shrinkage_raked` and calibrates region-specific
copies to vintage-correct Vid12 current-vote margins.

`R1 − T_nat` is the value of regional current-state information. `R1 −
B2_region` is the value of the voter-flow kernel given the same regional
polls. A win only against national B2 is not evidence of regional transition
structure. Secondary pooling is omitted because no vintage-correct historical
regional val-idag series is available for both holdouts.

## Survey-only lock

All regional estimator choices were committed before election scoring. The
2018 margins come from the original 2018-06-05 news table, not live revised
`Vid12` `2018M05`. The 2022 margins come from the 2022-06-02 news table and
match the pinned Vid12 extract. Region weights are full-Sweden
previous-election valid votes. The eight-group codebook is county-nested
except Stockholm municipality.

Approximate regional n_eff is the median margin-inverted cell size. It is not
Kish ESS, and it is not capped when the public regional base is missing.

| Cycle | Region | Approx. n_eff | Valid cells |
|---:|---|---:|---:|
| 2018 | 0180 | 887.3 | 9 |
| 2018 | SE01exkl0180 | 1326.3 | 9 |
| 2018 | SE06 | 952.3 | 9 |
| 2018 | SE07+SE08 | 952.7 | 9 |
| 2018 | SE09 | 888.6 | 9 |
| 2018 | SE0A | 1898.9 | 9 |
| 2018 | SE12 | 2086.6 | 9 |
| 2018 | SE2 | 1451.1 | 9 |
| 2022 | 0180 | 344.3 | 9 |
| 2022 | SE01exkl0180 | 437.2 | 9 |
| 2022 | SE06 | 333.5 | 8 |
| 2022 | SE07+SE08 | 339.7 | 8 |
| 2022 | SE09 | 362.7 | 8 |
| 2022 | SE0A | 807.9 | 9 |
| 2022 | SE12 | 640.1 | 9 |
| 2022 | SE2 | 504.5 | 9 |

## Locked point backtest

MAE is vote-weighted over district-party cells on the unchanged 4A/4B
populations. Negative ΔMAE is improvement.

| Election | Model | Weighted MAE (pp) |
|---:|---|---:|
| 2018 | B2_national | 1.978 |
| 2018 | B2_region | 2.107 |
| 2018 | R1_published_point | 2.044 |
| 2018 | R1_region_transition | 2.044 |
| 2018 | R1_suppressed_zero | 2.044 |
| 2018 | T_nat | 1.898 |
| 2022 | B2_national | 2.126 |
| 2022 | B2_region | 2.230 |
| 2022 | R1_published_point | 2.220 |
| 2022 | R1_region_transition | 2.219 |
| 2022 | R1_suppressed_zero | 2.270 |
| 2022 | T_nat | 2.026 |

| Election | Comparison | R1 MAE (pp) | Reference MAE (pp) | ΔMAE (pp) |
|---:|---|---:|---:|---:|
| 2018 | r1_minus_b2_national | 2.044 | 1.978 | +0.066 |
| 2018 | regional_value | 2.044 | 1.898 | +0.145 |
| 2018 | transition_value | 2.044 | 2.107 | -0.063 |
| 2022 | r1_minus_b2_national | 2.219 | 2.126 | +0.093 |
| 2022 | regional_value | 2.219 | 2.026 | +0.193 |
| 2022 | transition_value | 2.219 | 2.230 | -0.010 |

R1 is worse than the national 4B kernel in both years: +0.145 pp in 2018 and
+0.193 pp in 2022. That fails the regional-value gate on the point comparison
alone, so the locked verdict is NOT_SUPPORTED. R1 is slightly better than
regional proportional swing on the point estimate, but that cannot rescue the
gate. R1 is also worse than national B2. The published-point and
zero-suppression sensitivities stay close to the primary and do not change
the comparison.

## Survey-draw intervals

`regional_value` uses region-draw intervals with the national kernel fixed.
`transition_value` uses transition-draw intervals with regional margins
fixed. Joint intervals are a dependence approximation: both tables come from
the same PSU respondents and no public covariance is published.

| Cycle | Analysis | Comparison | Mean ΔMAE (pp) | 95% interval |
|---|---|---|---|---|
| 2018 | independent_joint_draws_labeled_dependence_approximation | regional_value | +0.235 | [+0.160, +0.323] |
| 2018 | independent_joint_draws_labeled_dependence_approximation | transition_value | -0.055 | [-0.075, -0.030] |
| 2018 | region_draws_fixed_transition | regional_value | +0.232 | [+0.157, +0.318] |
| 2018 | region_draws_fixed_transition | transition_value | +0.023 | [-0.052, +0.109] |
| 2018 | transition_draws_fixed_region_margins | regional_value | +0.148 | [+0.134, +0.164] |
| 2018 | transition_draws_fixed_region_margins | transition_value | -0.061 | [-0.075, -0.045] |
| 2022 | independent_joint_draws_labeled_dependence_approximation | regional_value | +0.390 | [+0.243, +0.558] |
| 2022 | independent_joint_draws_labeled_dependence_approximation | transition_value | +0.002 | [-0.034, +0.039] |
| 2022 | region_draws_fixed_transition | regional_value | +0.387 | [+0.242, +0.556] |
| 2022 | region_draws_fixed_transition | transition_value | +0.184 | [+0.039, +0.353] |
| 2022 | transition_draws_fixed_region_margins | regional_value | +0.195 | [+0.181, +0.210] |
| 2022 | transition_draws_fixed_region_margins | transition_value | -0.008 | [-0.022, +0.007] |

The region-draw intervals for `regional_value` lie entirely above zero in
both years. The transition-draw interval for `transition_value` is negative
in 2018 and crosses zero in 2022. None of this is used to choose a different
estimator after seeing the scores.

## Evaluation coverage

The frozen eval populations are 4,631 districts in 2018 and 4,164 in 2022.
Region weights remain full-Sweden previous-election valid votes.
Eval-population coverage is reported separately and does not select the
model.

| Cycle | Region | Eval coverage of previous votes |
|---|---|---|
| 2018 | 0180 | 66.2% |
| 2018 | SE01exkl0180 | 72.6% |
| 2018 | SE06 | 85.8% |
| 2018 | SE07+SE08 | 84.7% |
| 2018 | SE09 | 82.4% |
| 2018 | SE0A | 63.1% |
| 2018 | SE12 | 80.1% |
| 2018 | SE2 | 83.8% |
| 2022 | 0180 | 66.1% |
| 2022 | SE01exkl0180 | 70.2% |
| 2022 | SE06 | 84.1% |
| 2022 | SE07+SE08 | 61.5% |
| 2022 | SE09 | 72.6% |
| 2022 | SE0A | 53.3% |
| 2022 | SE12 | 64.3% |
| 2022 | SE2 | 69.5% |

## Geographic diagnostics

County splits and municipality-cluster bootstrap are diagnostics only.

| Transition | Model | Reference | Counties won | Winning vote coverage | Median county ΔMAE (pp) |
|---|---|---|---|---|---|
| 2014_2018 | R1_region_transition | B2_region | 17/21 | 92.1% | -0.069 |
| 2014_2018 | R1_region_transition | T_nat | 2/21 | 16.8% | +0.219 |
| 2018_2022 | R1_region_transition | B2_region | 14/21 | 49.9% | -0.040 |
| 2018_2022 | R1_region_transition | T_nat | 13/21 | 34.3% | -0.058 |

| Transition | Comparison | Observed ΔMAE (pp) | Bootstrap 95% |
|---|---|---|---|
| 2014_2018 | regional_value | +0.145 | [+0.045, +0.223] |
| 2014_2018 | transition_value | -0.063 | [-0.091, -0.035] |
| 2018_2022 | regional_value | +0.193 | [+0.129, +0.265] |
| 2018_2022 | transition_value | -0.010 | [-0.069, +0.055] |

## What this does not say

4C does not overturn 4B. The national stated-party kernel still beats
national proportional swing. What fails here is the added regional
current-state step: grafting SCB's eight published val-idag margins onto that
kernel makes the district forecast worse on the locked holdouts. 4C also does
not estimate a directly observed regional voter-flow interaction, and it does
not implement demographic MRP.
