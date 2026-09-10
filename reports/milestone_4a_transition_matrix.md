# Milestone 4A — Current-election voter-transition gate

## Frozen predecessor

Milestone 3 remains frozen at commit `ea5def3` with conclusion **FAIL**. No
CORE model, target or gate was retuned.

## Sources

The full source inventory is in `reports/poll_transition_availability.md`.
Only SCB PSU was verified to publish pre-election national previous-vote ×
current-intention tables. The original 2018 publication was recovered through
the Kungliga biblioteket URN resolver; its PDF Table 21 is parsed directly so
current post-2020 PxWeb revisions are not substituted. The strict waves ended
2018-05-29 and 2022-05-25 and were published 2018-06-11 and 2022-06-08.

Both 2018 and 2022 are executable strict holdouts. T-30 through T-1 are
unavailable because no full public transition matrices were found.
No second pollster exposes a comparable full table, so pollster disagreement
or precision-weighted pooling cannot be estimated in 4A.

## Evaluation population

| Transition | Eval n | Target n | District cov. | Eval votes | Vote cov. | Mapping |
|---|---:|---:|---:|---:|---:|---|
| 2014_2018 | 4631 | 6004 | 77.13% | 4858338 | 75.01% | HIGH |
| 2018_2022 | 4164 | 6264 | 66.48% | 4201935 | 64.86% | HIGH |

Coverage is not representative of all Sweden, especially in 2022. Detailed
metro, larger-town and rural included/excluded shares and population-growth
differences are retained in
`reports/milestone_four_a/selection_bias_by_urbanity.csv`.

## Transition matrix normalization

Published SCB percentages include blank, unknown and—in the 2018 original—
missing-response categories. T0 conditions on a stated party choice.
Suppressed 2022 cells retain their explicit status; remaining rounded row mass
is allocated according to the same-wave national poll prior. Rows are then
shrunk toward that prior using
`n / (n + 200)`.
`suppressed_zero` is reported only as a lower-bound sensitivity; it does not
reinterpret SCB's `..` cells as observed zeroes.
`nonparty_poll` instead allocates blank/unknown/missing mass by the national
poll before conditioning, testing the primary assumption that stated choosers
represent unresolved respondents within each previous-party row.

| Election | Prev. | n | Published | Omitted | Imputed | Nonparty | Reliability | Status |
|---:|---|---:|---:|---:|---:|---:|---:|---|
| 2018 | V | 253 | 9/9 | 0.0% | 0.0% | 16.7% | 55.8% | OBSERVED_CONDITIONED |
| 2018 | S | 1431 | 9/9 | 0.0% | 0.0% | 26.6% | 87.7% | OBSERVED_CONDITIONED |
| 2018 | MP | 325 | 9/9 | 0.0% | 0.0% | 32.0% | 61.9% | OBSERVED_CONDITIONED |
| 2018 | C | 295 | 9/9 | 0.0% | 0.0% | 21.0% | 59.6% | OBSERVED_CONDITIONED |
| 2018 | L | 283 | 9/9 | 0.0% | 0.0% | 28.3% | 58.6% | OBSERVED_CONDITIONED |
| 2018 | M | 1218 | 9/9 | 0.1% | 0.0% | 25.9% | 85.9% | OBSERVED_CONDITIONED |
| 2018 | KD | 184 | 9/9 | 0.0% | 0.0% | 21.9% | 47.9% | OBSERVED_CONDITIONED |
| 2018 | SD | 420 | 9/9 | 0.0% | 0.0% | 20.5% | 67.7% | OBSERVED_CONDITIONED |
| 2018 | OTHER | 143 | 9/9 | 0.0% | 0.0% | 20.3% | 41.7% | OBSERVED_CONDITIONED |
| 2022 | V | 318 | 6/9 | 2.3% | 2.3% | 9.7% | 61.4% | PARTIAL_CONSTRAINED_PRIOR |
| 2022 | S | 1137 | 8/9 | 0.4% | 0.4% | 13.7% | 85.0% | PARTIAL_CONSTRAINED_PRIOR |
| 2022 | MP | 184 | 7/9 | 1.9% | 1.9% | 11.5% | 47.9% | PARTIAL_CONSTRAINED_PRIOR |
| 2022 | C | 301 | 5/9 | 2.8% | 2.8% | 18.3% | 60.1% | PARTIAL_CONSTRAINED_PRIOR |
| 2022 | L | 263 | 9/9 | 0.0% | 0.0% | 16.5% | 56.8% | OBSERVED_CONDITIONED |
| 2022 | M | 801 | 6/9 | 0.9% | 0.9% | 12.0% | 80.0% | PARTIAL_CONSTRAINED_PRIOR |
| 2022 | KD | 219 | 7/9 | 1.0% | 1.0% | 17.5% | 52.3% | PARTIAL_CONSTRAINED_PRIOR |
| 2022 | SD | 490 | 7/9 | 0.8% | 0.8% | 7.8% | 71.0% | PARTIAL_CONSTRAINED_PRIOR |
| 2022 | OTHER | 51 | 5/9 | 21.7% | 21.7% | 16.2% | 20.3% | PARTIAL_CONSTRAINED_PRIOR |

The calibrated matrix is the minimum-KL iterative-raking solution whose rows
sum to one and whose national aggregate matches SCB's same-wave `val idag`
vector. No target-election result enters calibration.
Calibration uses the full-Sweden previous-election vector. Because the frozen
evaluation populations cover only 75.01% and 64.86% of votes, their aggregate
predictions are not expected to equal the national poll exactly.

| Election | Prior | Suppressed | Nonparty | Iterations | Max margin error | KL divergence |
|---:|---:|---|---|---:|---:|---:|
| 2018 | 0 | allocate_gap | condition | 35 | 9.85e-11 | 0.00178 |
| 2018 | 0 | allocate_gap | allocate_poll | 16 | 8.09e-11 | 0.00184 |
| 2018 | 200 | allocate_gap | condition | 21 | 4.44e-11 | 0.00407 |
| 2018 | 1000 | allocate_gap | condition | 11 | 2.19e-11 | 0.00581 |
| 2022 | 0 | allocate_gap | condition | 48 | 8.46e-11 | 0.00356 |
| 2022 | 0 | zero_sensitivity | condition | 57 | 8.19e-11 | 0.00446 |
| 2022 | 0 | allocate_gap | allocate_poll | 30 | 9.81e-11 | 0.00300 |
| 2022 | 200 | allocate_gap | condition | 20 | 3.90e-11 | 0.00675 |
| 2022 | 1000 | allocate_gap | condition | 10 | 1.61e-11 | 0.00436 |

### Normalized and calibrated transition matrices

Rows are previous-election party and columns are current intention.

#### scb_2018M05_original — NORMALIZED_RAW

| Previous | V | S | MP | C | L | M | KD | SD | OTHER |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| V | 0.773 | 0.100 | 0.024 | 0.024 | 0.000 | 0.010 | 0.005 | 0.032 | 0.032 |
| S | 0.040 | 0.775 | 0.016 | 0.019 | 0.012 | 0.041 | 0.004 | 0.083 | 0.010 |
| MP | 0.106 | 0.204 | 0.394 | 0.119 | 0.037 | 0.059 | 0.009 | 0.031 | 0.041 |
| C | 0.013 | 0.043 | 0.005 | 0.709 | 0.038 | 0.111 | 0.013 | 0.056 | 0.013 |
| L | 0.000 | 0.054 | 0.010 | 0.113 | 0.536 | 0.196 | 0.035 | 0.025 | 0.031 |
| M | 0.005 | 0.023 | 0.005 | 0.073 | 0.031 | 0.719 | 0.011 | 0.123 | 0.009 |
| KD | 0.000 | 0.014 | 0.015 | 0.137 | 0.028 | 0.146 | 0.547 | 0.098 | 0.014 |
| SD | 0.009 | 0.006 | 0.000 | 0.003 | 0.003 | 0.048 | 0.006 | 0.913 | 0.013 |
| OTHER | 0.320 | 0.148 | 0.069 | 0.060 | 0.000 | 0.025 | 0.009 | 0.035 | 0.335 |

#### scb_2018M05_original — CALIBRATED_200

| Previous | V | S | MP | C | L | M | KD | SD | OTHER |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| V | 0.496 | 0.150 | 0.037 | 0.053 | 0.020 | 0.095 | 0.017 | 0.095 | 0.037 |
| S | 0.053 | 0.677 | 0.026 | 0.032 | 0.018 | 0.065 | 0.009 | 0.104 | 0.016 |
| MP | 0.100 | 0.195 | 0.299 | 0.109 | 0.040 | 0.111 | 0.018 | 0.086 | 0.044 |
| C | 0.041 | 0.119 | 0.024 | 0.478 | 0.042 | 0.146 | 0.022 | 0.106 | 0.024 |
| L | 0.034 | 0.128 | 0.028 | 0.107 | 0.345 | 0.195 | 0.037 | 0.090 | 0.037 |
| M | 0.017 | 0.053 | 0.013 | 0.082 | 0.036 | 0.631 | 0.016 | 0.135 | 0.016 |
| KD | 0.042 | 0.130 | 0.035 | 0.114 | 0.037 | 0.171 | 0.308 | 0.138 | 0.026 |
| SD | 0.033 | 0.083 | 0.017 | 0.032 | 0.017 | 0.100 | 0.016 | 0.680 | 0.022 |
| OTHER | 0.189 | 0.189 | 0.062 | 0.077 | 0.026 | 0.129 | 0.023 | 0.117 | 0.187 |

#### scb_2022M05 — NORMALIZED_RAW

| Previous | V | S | MP | C | L | M | KD | SD | OTHER |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| V | 0.723 | 0.209 | 0.024 | 0.000 | 0.000 | 0.019 | 0.005 | 0.018 | 0.002 |
| S | 0.035 | 0.870 | 0.012 | 0.014 | 0.005 | 0.028 | 0.008 | 0.024 | 0.005 |
| MP | 0.078 | 0.364 | 0.419 | 0.042 | 0.026 | 0.050 | 0.000 | 0.019 | 0.002 |
| C | 0.009 | 0.242 | 0.004 | 0.567 | 0.039 | 0.097 | 0.021 | 0.019 | 0.002 |
| L | 0.000 | 0.214 | 0.025 | 0.124 | 0.360 | 0.232 | 0.027 | 0.018 | 0.000 |
| M | 0.006 | 0.073 | 0.003 | 0.020 | 0.018 | 0.736 | 0.059 | 0.083 | 0.002 |
| KD | 0.009 | 0.074 | 0.004 | 0.050 | 0.017 | 0.221 | 0.547 | 0.061 | 0.019 |
| SD | 0.006 | 0.037 | 0.003 | 0.000 | 0.000 | 0.090 | 0.035 | 0.808 | 0.022 |
| OTHER | 0.222 | 0.084 | 0.018 | 0.000 | 0.000 | 0.118 | 0.029 | 0.094 | 0.436 |

#### scb_2022M05 — CALIBRATED_200

| Previous | V | S | MP | C | L | M | KD | SD | OTHER |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| V | 0.506 | 0.219 | 0.037 | 0.027 | 0.015 | 0.087 | 0.022 | 0.071 | 0.014 |
| S | 0.049 | 0.755 | 0.022 | 0.026 | 0.012 | 0.058 | 0.016 | 0.048 | 0.012 |
| MP | 0.081 | 0.289 | 0.284 | 0.057 | 0.035 | 0.122 | 0.026 | 0.089 | 0.018 |
| C | 0.040 | 0.241 | 0.021 | 0.395 | 0.045 | 0.135 | 0.033 | 0.075 | 0.015 |
| L | 0.036 | 0.226 | 0.038 | 0.105 | 0.258 | 0.208 | 0.037 | 0.078 | 0.014 |
| M | 0.023 | 0.113 | 0.012 | 0.033 | 0.027 | 0.624 | 0.060 | 0.099 | 0.009 |
| KD | 0.046 | 0.173 | 0.024 | 0.063 | 0.031 | 0.208 | 0.314 | 0.109 | 0.032 |
| SD | 0.030 | 0.110 | 0.016 | 0.022 | 0.012 | 0.123 | 0.041 | 0.611 | 0.035 |
| OTHER | 0.112 | 0.235 | 0.039 | 0.055 | 0.031 | 0.177 | 0.045 | 0.141 | 0.163 |

All stages, including shrinkage sensitivities, are in
`reports/milestone_four_a/transition_matrices.csv`.

## Backtest

B2 and T0 receive exactly the same May national poll state within each wave.
Actual target-election results and valid votes are evaluation-only. Negative
ΔMAE is improvement over B2.

| Election | Cutoff | Model | District weighted MAE | Eval-pop aggregate MAE | ΔMAE vs B2 |
|---:|---|---|---:|---:|---:|
| 2018 | 2018-06-11 | B2_poll_state | 0.01978 | 0.01116 | +0.00000 |
| 2018 | 2018-06-11 | T0_calibrated | 0.02280 | 0.01098 | +0.00302 |
| 2018 | 2018-06-11 | T0_calibrated_nonparty_poll | 0.02210 | 0.01119 | +0.00233 |
| 2018 | 2018-06-11 | T0_calibrated_prior_0 | 0.01899 | 0.01123 | -0.00079 |
| 2018 | 2018-06-11 | T0_calibrated_prior_1000 | 0.02965 | 0.01085 | +0.00987 |
| 2018 | 2018-06-11 | T0_raw | 0.01771 | 0.01046 | -0.00206 |
| 2018 | 2018-06-11 | T0_shrunk_uncalibrated | 0.02417 | 0.01577 | +0.00440 |
| 2022 | 2022-06-08 | B2_poll_state | 0.02126 | 0.01500 | +0.00000 |
| 2022 | 2022-06-08 | T0_calibrated | 0.02353 | 0.01556 | +0.00227 |
| 2022 | 2022-06-08 | T0_calibrated_nonparty_poll | 0.02131 | 0.01538 | +0.00005 |
| 2022 | 2022-06-08 | T0_calibrated_prior_0 | 0.02032 | 0.01532 | -0.00094 |
| 2022 | 2022-06-08 | T0_calibrated_prior_1000 | 0.02945 | 0.01596 | +0.00819 |
| 2022 | 2022-06-08 | T0_calibrated_suppressed_zero | 0.02017 | 0.01532 | -0.00109 |
| 2022 | 2022-06-08 | T0_raw | 0.02074 | 0.01612 | -0.00052 |
| 2022 | 2022-06-08 | T0_shrunk_uncalibrated | 0.02657 | 0.02007 | +0.00531 |

The national poll itself has the following all-Sweden MAE against the eventual
result. It is a source diagnostic and is identical input to every model.

| Election | National poll MAE |
|---:|---:|
| 2018 | 0.01162 |
| 2022 | 0.01484 |

Current-share error and local swing-residual error are algebraically identical
for each district-party cell.

## Per-party error

| Transition | Party | Model | Weighted MAE |
|---|---|---|---:|
| 2014_2018 | C | B2_poll_state | 0.01938 |
| 2014_2018 | KD | B2_poll_state | 0.03508 |
| 2014_2018 | L | B2_poll_state | 0.01107 |
| 2014_2018 | M | B2_poll_state | 0.03495 |
| 2014_2018 | MP | B2_poll_state | 0.00777 |
| 2014_2018 | OTHER | B2_poll_state | 0.01358 |
| 2014_2018 | S | B2_poll_state | 0.02287 |
| 2014_2018 | SD | B2_poll_state | 0.02031 |
| 2014_2018 | V | B2_poll_state | 0.01296 |
| 2014_2018 | C | T0_calibrated | 0.01709 |
| 2014_2018 | KD | T0_calibrated | 0.03531 |
| 2014_2018 | L | T0_calibrated | 0.01452 |
| 2014_2018 | M | T0_calibrated | 0.03353 |
| 2014_2018 | MP | T0_calibrated | 0.01201 |
| 2014_2018 | OTHER | T0_calibrated | 0.01382 |
| 2014_2018 | S | T0_calibrated | 0.02672 |
| 2014_2018 | SD | T0_calibrated | 0.03267 |
| 2014_2018 | V | T0_calibrated | 0.01951 |
| 2018_2022 | C | B2_poll_state | 0.01262 |
| 2018_2022 | KD | B2_poll_state | 0.01168 |
| 2018_2022 | L | B2_poll_state | 0.01230 |
| 2018_2022 | M | B2_poll_state | 0.03043 |
| 2018_2022 | MP | B2_poll_state | 0.01761 |
| 2018_2022 | OTHER | B2_poll_state | 0.00968 |
| 2018_2022 | S | B2_poll_state | 0.04103 |
| 2018_2022 | SD | B2_poll_state | 0.03920 |
| 2018_2022 | V | B2_poll_state | 0.01679 |
| 2018_2022 | C | T0_calibrated | 0.01188 |
| 2018_2022 | KD | T0_calibrated | 0.01378 |
| 2018_2022 | L | T0_calibrated | 0.01472 |
| 2018_2022 | M | T0_calibrated | 0.03094 |
| 2018_2022 | MP | T0_calibrated | 0.01987 |
| 2018_2022 | OTHER | T0_calibrated | 0.01004 |
| 2018_2022 | S | T0_calibrated | 0.03400 |
| 2018_2022 | SD | T0_calibrated | 0.05162 |
| 2018_2022 | V | T0_calibrated | 0.02489 |

## Bloc error

| Transition | Bloc | Model | Weighted MAE |
|---|---|---|---:|
| 2014_2018 | ALLIANCE_2018_TAXONOMY | B2_poll_state | 0.03019 |
| 2014_2018 | RED_GREEN_2018_TAXONOMY | B2_poll_state | 0.02868 |
| 2014_2018 | ALLIANCE_2018_TAXONOMY | T0_calibrated | 0.03674 |
| 2014_2018 | RED_GREEN_2018_TAXONOMY | T0_calibrated | 0.04042 |
| 2018_2022 | ALLIANCE_2018_TAXONOMY | B2_poll_state | 0.02694 |
| 2018_2022 | RED_GREEN_2018_TAXONOMY | B2_poll_state | 0.04320 |
| 2018_2022 | ALLIANCE_2018_TAXONOMY | T0_calibrated | 0.03543 |
| 2018_2022 | RED_GREEN_2018_TAXONOMY | T0_calibrated | 0.04692 |

## Geographic robustness

| Transition | Model | Counties won | Winning vote coverage | Median county ΔMAE |
|---|---|---:|---:|---:|
| 2014_2018 | T0_calibrated | 0/21 | 0.0% | +0.00332 |
| 2014_2018 | T0_raw | 17/21 | 90.6% | -0.00140 |
| 2018_2022 | T0_calibrated | 7/21 | 15.5% | +0.00128 |
| 2018_2022 | T0_raw | 17/21 | 64.9% | -0.00087 |

District-size and fixed SKR-2017 municipality-type breakdowns are retained in
the machine-readable report directory.

## Uncertainty and shrinkage sensitivity

Municipality-cluster bootstrap uses 2,000 seeded replicates.

| Transition | Model | Observed ΔMAE | 95% interval |
|---|---|---:|---:|
| 2014_2018 | T0_calibrated | +0.00302 | [+0.00253, +0.00356] |
| 2014_2018 | T0_calibrated_prior_0 | -0.00079 | [-0.00127, -0.00032] |
| 2014_2018 | T0_calibrated_prior_1000 | +0.00987 | [+0.00902, +0.01070] |
| 2014_2018 | T0_calibrated_nonparty_poll | +0.00233 | [+0.00184, +0.00289] |
| 2018_2022 | T0_calibrated | +0.00227 | [+0.00154, +0.00294] |
| 2018_2022 | T0_calibrated_prior_0 | -0.00094 | [-0.00125, -0.00058] |
| 2018_2022 | T0_calibrated_prior_1000 | +0.00819 | [+0.00686, +0.00958] |
| 2018_2022 | T0_calibrated_suppressed_zero | -0.00109 | [-0.00141, -0.00079] |
| 2018_2022 | T0_calibrated_nonparty_poll | +0.00005 | [-0.00039, +0.00044] |

## Why Milestone 3 failed

The same official district chains show weak or unstable residual rank
relationships across regimes:

| Party/bloc | Matched mapping links | Pearson residual correlation | Spearman rank correlation |
|---|---:|---:|---:|
| V | 3347 | +0.046 | -0.045 |
| S | 3347 | +0.205 | +0.318 |
| MP | 3347 | -0.057 | -0.126 |
| C | 3347 | +0.414 | +0.350 |
| L | 3347 | -0.376 | -0.331 |
| M | 3347 | +0.268 | +0.258 |
| KD | 3347 | +0.312 | +0.301 |
| SD | 3347 | +0.027 | +0.017 |
| OTHER | 3347 | +0.002 | -0.158 |
| LEFT_BLOC | 3346 | +0.422 | +0.444 |
| ALLIANCE_BLOC | 3346 | +0.234 | +0.250 |

This is diagnostic only; no Milestone 3 model was changed.
Official split mappings are exploded to source-target links, so this count is
not a count of independent districts.
The frozen M3 feature-drift artifact also records sign changes for MP, V and SD
between the 2014→2018 and 2018→2022 regimes. The Spearman column above is the
district-sensitivity ranking comparison.

## Voter-flow stability

Raw conditioned transition rows also change between the May 2018 and May 2022
cycles. Total-variation distance is zero only for identical rows.

| Previous party | Cell correlation | Total-variation distance | Largest cell change |
|---|---:|---:|---:|
| V | +0.984 | 0.119 | 0.110 |
| S | +0.997 | 0.099 | 0.095 |
| MP | +0.929 | 0.185 | 0.159 |
| C | +0.927 | 0.209 | 0.199 |
| L | +0.871 | 0.222 | 0.177 |
| M | +0.989 | 0.116 | 0.053 |
| KD | +0.962 | 0.149 | 0.087 |
| SD | +0.998 | 0.113 | 0.105 |
| OTHER | +0.858 | 0.273 | 0.101 |

This comparison is national and unconditional on demographics; the public
strict tables do not support a valid age-specific cross-cycle comparison.

## Limitations

T0 can poststratify only on previous party vote shares known by district. The
2018 table also contains previous non-voter and newly eligible columns, but no
district-level joint distribution exists for those groups; they are excluded.
National calibration therefore absorbs their national contribution into the
previous-party rows rather than locating it geographically. Survey weights and
effective cell counts are not public, and the 2018 `Antal i urvalet` values are
gross previous-party column bases rather than effective sample sizes.

## Gate A

**UNCLEAR**

| Election | T0 calibrated | B2 | ΔMAE | Counties won | Municipality-bootstrap 95% |
|---:|---:|---:|---:|---:|---:|
| 2018 | 2.280 | 1.978 | +0.302 | 0/21 | [+0.253, +0.356] |
| 2022 | 2.353 | 2.126 | +0.227 | 7/21 | [+0.154, +0.294] |

The primary `n/(n+200)` matrix loses to B2 in both cycles, while calibrated
no-shrink T0 has ΔMAE 2018: -0.079 pp; 2022: -0.094 pp. Both no-shrink improvements have
negative municipality-bootstrap intervals. The 2022 lower-bound suppression
sensitivity has ΔMAE -0.109 pp, so the favorable no-shrink
result is not driven by allocating suppressed mass. The α=200 rule was fixed
in code before the backtest was run, but was not externally preregistered.
Allocating blank/unknown/missing responses by the poll prior gives
2018: +0.233 pp; 2022: +0.005 pp. Because these defensible treatment choices reverse or erase
the gain, the transition signal is real enough to investigate but not robust
enough to pass Gate A.

This result tests national voter-flow information from strict May 2018 and May
2022 waves. The gate also requires stability across declared shrinkage
sensitivities and cycles. It does not validate regional transitions,
demographics, MRP or a 2026 forecast. Those stages remain blocked.
