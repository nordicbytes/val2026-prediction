# Milestone 4B — Survey-based transition estimator

## Status

Milestone 4A remains byte-for-byte frozen as **UNCLEAR**. Its report SHA-256 is
`cee6d058647c9b0cfd4a3d01f5dda4ec31ee25a473b13f9be832072016c6cab3`.
This is a separate 4B result and does not revise 4A.

## Why the 4A prior was misspecified

The 4A `n/(n+200)` estimator pulled every previous-party row toward the same
current-party national marginal. That mechanically reduces the
`previous_party → current_party` dependence T0 exists to measure. It was
generic regularization, but not a coherent origin-specific transition prior.

The locked 4B primary leaves SCB's survey-weighted published proportions
unchanged as point estimates. Sampling uncertainty is represented by
deterministic Dirichlet draws using margin-inverted approximate effective
sample sizes. These are explicitly not Kish ESS. Every draw is conditioned on
a stated party and raked separately. Same-wave Vid10 enters only raking and B2.

## Survey-only lock

All estimator choices were committed before election scoring. The historical
corpus contains 14 vintage-correct waves and 1,932 cells. Original PDF
vintages are used for the 2018 hierarchy; the revised series available from
2020 is used for 2022. `2018M05` and `2022M05` never enter prior fitting.
The secondary prior is previous-party-specific, and κ was selected with
whole-year-held-out survey waves only. `2017M11` and `2021M11` are untouched
survey audits, not tuning data.

## Approximate effective sample sizes

| Election | Previous party | Public base | Approx. n_eff | Implied design effect | Fallback |
|---:|---|---:|---:|---:|---|
| 2018 | C | 295 | 295.0 | 1.00 | no |
| 2018 | KD | 184 | 183.7 | 1.00 | no |
| 2018 | L | 283 | 283.0 | 1.00 | no |
| 2018 | M | 1218 | 1218.0 | 1.00 | no |
| 2018 | MP | 325 | 323.4 | 1.01 | no |
| 2018 | OTHER | 143 | 143.0 | 1.00 | no |
| 2018 | S | 1431 | 1410.0 | 1.01 | no |
| 2018 | SD | 420 | 413.3 | 1.02 | no |
| 2018 | V | 253 | 252.9 | 1.00 | no |
| 2022 | C | 301 | 283.9 | 1.06 | no |
| 2022 | KD | 219 | 207.7 | 1.05 | no |
| 2022 | L | 263 | 235.4 | 1.12 | no |
| 2022 | M | 801 | 717.7 | 1.12 | no |
| 2022 | MP | 184 | 168.2 | 1.09 | no |
| 2022 | OTHER | 51 | 50.5 | 1.01 | no |
| 2022 | S | 1137 | 980.2 | 1.16 | no |
| 2022 | SD | 490 | 467.3 | 1.05 | no |
| 2022 | V | 318 | 278.1 | 1.14 | no |

## Locked point backtest

MAE is vote-weighted over district-party cells on the unchanged 4A populations.
B2 and every transition model receive exactly the same May national poll
vector. Negative ΔMAE is improvement. The primary point forecast is the mean
of 2,000 independently raked survey draws.

| Election | Model | Weighted MAE (pp) | B2 MAE (pp) | ΔMAE (pp) |
|---:|---|---:|---:|---:|
| 2018 | B2_poll_state | 1.978 | 1.978 | +0.000 |
| 2018 | T1_no_point_shrinkage_raked | 1.898 | 1.978 | -0.079 |
| 2018 | T1_nonparty_poll_point | 2.210 | 1.978 | +0.233 |
| 2018 | T1_previous_party_hierarchical_raked | 1.898 | 1.978 | -0.079 |
| 2018 | T1_published_point_raked | 1.899 | 1.978 | -0.079 |
| 2022 | B2_poll_state | 2.126 | 2.126 | +0.000 |
| 2022 | T1_no_point_shrinkage_raked | 2.026 | 2.126 | -0.100 |
| 2022 | T1_nonparty_poll_point | 2.131 | 2.126 | +0.005 |
| 2022 | T1_previous_party_hierarchical_raked | 2.023 | 2.126 | -0.103 |
| 2022 | T1_published_point_raked | 2.027 | 2.126 | -0.099 |
| 2022 | T1_suppressed_historical_point | 2.027 | 2.126 | -0.099 |
| 2022 | T1_suppressed_zero_point | 2.017 | 2.126 | -0.109 |

`T1_published_point_raked` isolates the directly raked published matrix.
Suppression and nonparty variants are preregistered diagnostics. The
hierarchical estimator remains secondary and cannot replace the primary.
The nonparty-allocation sensitivity loses to B2 in 2018 and is effectively
flat in 2022. `SUPPORTED` therefore applies to the locked stated-party
estimand; it is not robustness to assumptions about unresolved respondents.

## Survey uncertainty

These intervals vary transition-table sampling uncertainty while holding the
May national poll state fixed. They are not election-prediction intervals and
exclude poll-state error, correlated panel error and weight-estimation error.

| Election | Model | Mean draw ΔMAE (pp) | Survey-draw 95% interval | Draws |
|---:|---|---:|---:|---:|
| 2018 | T1_no_point_shrinkage_raked | -0.076 | [-0.096, -0.053] | 2000 |
| 2018 | T1_previous_party_hierarchical_raked | -0.077 | [-0.093, -0.060] | 2000 |
| 2022 | T1_no_point_shrinkage_raked | -0.097 | [-0.117, -0.075] | 2000 |
| 2022 | T1_previous_party_hierarchical_raked | -0.101 | [-0.119, -0.083] | 2000 |

## Geographic robustness

| Transition | Model | Counties won | Winning vote coverage | Median county ΔMAE (pp) |
|---|---|---:|---:|---:|
| 2014_2018 | T1_no_point_shrinkage_raked | 12/21 | 77.9% | -0.021 |
| 2014_2018 | T1_previous_party_hierarchical_raked | 13/21 | 81.3% | -0.027 |
| 2018_2022 | T1_no_point_shrinkage_raked | 16/21 | 63.3% | -0.118 |
| 2018_2022 | T1_previous_party_hierarchical_raked | 16/21 | 63.3% | -0.117 |

The municipality-cluster bootstrap is separate from survey uncertainty and
does not select κ or an estimator.

| Transition | Model | Point ΔMAE (pp) | Municipality-bootstrap 95% |
|---|---|---:|---:|
| 2014_2018 | T1_no_point_shrinkage_raked | -0.079 | [-0.125, -0.029] |
| 2014_2018 | T1_previous_party_hierarchical_raked | -0.079 | [-0.123, -0.036] |
| 2018_2022 | T1_no_point_shrinkage_raked | -0.100 | [-0.131, -0.066] |
| 2018_2022 | T1_previous_party_hierarchical_raked | -0.103 | [-0.131, -0.071] |

## Decision

**SUPPORTED**

The locked rule is `SUPPORTED` only if primary point ΔMAE is negative and the
survey-draw 95% upper bound is below zero in both elections; `NOT_SUPPORTED`
if either primary point ΔMAE is non-negative; otherwise `UNCLEAR`.

This tests transition estimation only. It does not validate regional
conditioning, demographic poststratification or MRP, and no MRP implementation
is started here.
