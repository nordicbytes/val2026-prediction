# Milestone 3 — Temporal Generalization

## Frozen predecessor

Milestone 2 is frozen at tag `structural-signal-2018-2022-v1`.
Its strict model uses election-history CORE features only. SCB population is
selection-bias diagnostics, not model input.

## Data

| Transition | Districts | District coverage | Vote coverage | Mapping | Quality |
|---|---:|---:|---:|---|---|
| 2010_2014 | 4319 | 74.0% | 72.3% | STABLE_ID_NAME_EXACT | MEDIUM |
| 2014_2018 | 4631 | 77.1% | 75.0% | OFFICIAL_COMPARABLE + OFFICIAL_MERGE | HIGH |
| 2018_2022 | 4164 | 66.5% | 64.9% | OFFICIAL_COMPARABLE + OFFICIAL_MERGE + OFFICIAL_SPLIT_COMPARABLE | HIGH |

The 2010→2014 fallback requires both an identical official district code and
an identical normalized district name. It is a MEDIUM, sensitivity-only
inference, not an official comparison file. The later transitions use official
Valmyndigheten classifications. Exact 2014→2018 publication timing is not
machine-verified; the 2018→2022 file is retrospective/post-election and defines
a backtest evaluation population, not a live forecast universe. The 2018→2022
population is exactly 4,153
officially comparable targets plus
11 merged districts, with
64.86% vote coverage.
2 of the comparable
targets share one
officially comparable predecessor and are explicitly marked
`OFFICIAL_SPLIT_COMPARABLE`; this preserves the frozen Milestone 1 evaluation
population but is not a one-to-one identity. The 2006→2010 transition remains
unavailable because no official pair mapping or GIS reconstruction was
established. Results never represent every Swedish district.

| Transition | SKR 2017 group | Included | District share | Vote share | Pop. change |
|---|---|---|---:|---:|---:|
| 2010_2014 | Mindre orter och landsbygd | False | 8.6% | 8.7% | -0.1% |
| 2010_2014 | Mindre orter och landsbygd | True | 91.4% | 91.3% | 0.0% |
| 2010_2014 | Storstäder och storstadsnära | False | 43.0% | 42.6% | 6.3% |
| 2010_2014 | Storstäder och storstadsnära | True | 57.0% | 57.4% | 6.1% |
| 2010_2014 | Större städer och närkommuner | False | 23.0% | 21.9% | 3.8% |
| 2010_2014 | Större städer och närkommuner | True | 77.0% | 78.1% | 2.5% |
| 2014_2018 | Mindre orter och landsbygd | False | 11.8% | 13.0% | 3.2% |
| 2014_2018 | Mindre orter och landsbygd | True | 88.2% | 87.0% | 2.9% |
| 2014_2018 | Storstäder och storstadsnära | False | 31.6% | 30.4% | 6.6% |
| 2014_2018 | Storstäder och storstadsnära | True | 68.4% | 69.6% | 6.2% |
| 2014_2018 | Större städer och närkommuner | False | 22.3% | 21.9% | 5.2% |
| 2014_2018 | Större städer och närkommuner | True | 77.7% | 78.1% | 4.8% |
| 2018_2022 | Mindre orter och landsbygd | False | 19.0% | 19.5% | 1.9% |
| 2018_2022 | Mindre orter och landsbygd | True | 81.0% | 80.5% | 0.8% |
| 2018_2022 | Storstäder och storstadsnära | False | 38.6% | 36.4% | 4.7% |
| 2018_2022 | Storstäder och storstadsnära | True | 61.4% | 63.6% | 4.6% |
| 2018_2022 | Större städer och närkommuner | False | 38.2% | 38.3% | 3.7% |
| 2018_2022 | Större städer och närkommuner | True | 61.8% | 61.7% | 3.4% |

SKR 2017 groups are held fixed solely for a comparable descriptive
metro/rural split and are not model features. Population changes use the two
pre-election 31 December stocks surrounding each transition. Separate included
and excluded rows are retained in the machine-readable output.

CORE consists of prior party shares, turnout, entropy, concentration, fixed
party-group summaries, largest-party margin and electorate size. RICH is
explicitly reserved for later SCB demographic enrichment and is absent here.

## Baselines

| Transition | Baseline | Weighted MAE |
|---|---|---:|
| 2010_2014 | B0_no_change | 0.02843 |
| 2010_2014 | B1_uniform_swing | 0.01534 |
| 2010_2014 | B2_proportional_swing | 0.01395 |
| 2014_2018 | B0_no_change | 0.02794 |
| 2014_2018 | B1_uniform_swing | 0.01710 |
| 2014_2018 | B2_proportional_swing | 0.01465 |
| 2018_2022 | B0_no_change | 0.01955 |
| 2018_2022 | B1_uniform_swing | 0.01554 |
| 2018_2022 | B2_proportional_swing | 0.01469 |

All district models receive realized national party swing as deliberate oracle
input. Target-election valid votes are evaluation weights only, never model
features. Both roles are recorded in the canonical dataset.

## Descriptive residual structure

| Transition | PC1 | PC2 | Largest absolute PC1 loading |
|---|---:|---:|---|
| 2010_2014 | 36.4% | 21.8% | S |
| 2014_2018 | 34.3% | 21.3% | S |
| 2018_2022 | 51.3% | 18.2% | S |

Party residual standard deviations, full correlations and PCA loadings are in
`reports/milestone_three/`. Sum-to-zero closure mechanically induces negative
correlations, so PC1 is not automatically a substantive left-right factor.

## Temporal models

All preprocessing is fit on training transitions only. No test-transition
target is used in scaling, imputation, encoding or fitting. LightGBM with the
exact frozen Milestone 2 feature specification is the primary model; Ridge and
ElasticNet are comparators. B0 (official 2014→2018 only, tested on
2018→2022) is the strict primary gate. A and pooled B use the non-official
MEDIUM stable-ID 2010→2014 mapping and are sensitivity tests.

| Train | Test | Ablation | Model | Weighted MAE | Baseline | ΔMAE |
|---|---|---|---|---:|---:|---:|
| 2014_2018 | 2018_2022 | M2_frozen | ridge | 0.01684 | 0.01469 | 0.00215 |
| 2014_2018 | 2018_2022 | M2_frozen | elastic_net | 0.01676 | 0.01469 | 0.00207 |
| 2014_2018 | 2018_2022 | M2_frozen | lightgbm | 0.01667 | 0.01469 | 0.00198 |
| 2014_2018 | 2018_2022 | A_previous_shares | ridge | 0.01681 | 0.01469 | 0.00211 |
| 2014_2018 | 2018_2022 | A_previous_shares | elastic_net | 0.01666 | 0.01469 | 0.00197 |
| 2014_2018 | 2018_2022 | A_previous_shares | lightgbm | 0.01670 | 0.01469 | 0.00201 |
| 2014_2018 | 2018_2022 | B_plus_structure | ridge | 0.01703 | 0.01469 | 0.00234 |
| 2014_2018 | 2018_2022 | B_plus_structure | elastic_net | 0.01668 | 0.01469 | 0.00199 |
| 2014_2018 | 2018_2022 | B_plus_structure | lightgbm | 0.01677 | 0.01469 | 0.00208 |
| 2014_2018 | 2018_2022 | C_plus_turnout_size | ridge | 0.01705 | 0.01469 | 0.00236 |
| 2014_2018 | 2018_2022 | C_plus_turnout_size | elastic_net | 0.01675 | 0.01469 | 0.00206 |
| 2014_2018 | 2018_2022 | C_plus_turnout_size | lightgbm | 0.01668 | 0.01469 | 0.00199 |
| 2010_2014 | 2014_2018 | M2_frozen | ridge | 0.02200 | 0.01465 | 0.00735 |
| 2010_2014 | 2014_2018 | M2_frozen | elastic_net | 0.02175 | 0.01465 | 0.00710 |
| 2010_2014 | 2014_2018 | M2_frozen | lightgbm | 0.01975 | 0.01465 | 0.00510 |
| 2010_2014 | 2014_2018 | A_previous_shares | ridge | 0.02244 | 0.01465 | 0.00779 |
| 2010_2014 | 2014_2018 | A_previous_shares | elastic_net | 0.02224 | 0.01465 | 0.00758 |
| 2010_2014 | 2014_2018 | A_previous_shares | lightgbm | 0.01990 | 0.01465 | 0.00524 |
| 2010_2014 | 2014_2018 | B_plus_structure | ridge | 0.02261 | 0.01465 | 0.00795 |
| 2010_2014 | 2014_2018 | B_plus_structure | elastic_net | 0.02231 | 0.01465 | 0.00765 |
| 2010_2014 | 2014_2018 | B_plus_structure | lightgbm | 0.01981 | 0.01465 | 0.00516 |
| 2010_2014 | 2014_2018 | C_plus_turnout_size | ridge | 0.02208 | 0.01465 | 0.00743 |
| 2010_2014 | 2014_2018 | C_plus_turnout_size | elastic_net | 0.02177 | 0.01465 | 0.00712 |
| 2010_2014 | 2014_2018 | C_plus_turnout_size | lightgbm | 0.01974 | 0.01465 | 0.00509 |
| 2010_2014 + 2014_2018 | 2018_2022 | M2_frozen | ridge | 0.01663 | 0.01469 | 0.00194 |
| 2010_2014 + 2014_2018 | 2018_2022 | M2_frozen | elastic_net | 0.01629 | 0.01469 | 0.00160 |
| 2010_2014 + 2014_2018 | 2018_2022 | M2_frozen | lightgbm | 0.01548 | 0.01469 | 0.00079 |
| 2010_2014 + 2014_2018 | 2018_2022 | A_previous_shares | ridge | 0.01648 | 0.01469 | 0.00179 |
| 2010_2014 + 2014_2018 | 2018_2022 | A_previous_shares | elastic_net | 0.01624 | 0.01469 | 0.00155 |
| 2010_2014 + 2014_2018 | 2018_2022 | A_previous_shares | lightgbm | 0.01556 | 0.01469 | 0.00087 |
| 2010_2014 + 2014_2018 | 2018_2022 | B_plus_structure | ridge | 0.01655 | 0.01469 | 0.00185 |
| 2010_2014 + 2014_2018 | 2018_2022 | B_plus_structure | elastic_net | 0.01600 | 0.01469 | 0.00131 |
| 2010_2014 + 2014_2018 | 2018_2022 | B_plus_structure | lightgbm | 0.01545 | 0.01469 | 0.00076 |
| 2010_2014 + 2014_2018 | 2018_2022 | C_plus_turnout_size | ridge | 0.01670 | 0.01469 | 0.00201 |
| 2010_2014 + 2014_2018 | 2018_2022 | C_plus_turnout_size | elastic_net | 0.01622 | 0.01469 | 0.00153 |
| 2010_2014 + 2014_2018 | 2018_2022 | C_plus_turnout_size | lightgbm | 0.01556 | 0.01469 | 0.00086 |

Negative ΔMAE is improvement over proportional national swing.

## Primary LightGBM per party

| Test ID | Test transition | Party | Model MAE | Baseline MAE | ΔMAE |
|---|---|---|---:|---:|---:|
| A | 2014_2018 | C | 0.01965 | 0.01919 | 0.00046 |
| A | 2014_2018 | KD | 0.01350 | 0.01419 | -0.00068 |
| A | 2014_2018 | L | 0.00926 | 0.00845 | 0.00081 |
| A | 2014_2018 | M | 0.01987 | 0.02101 | -0.00114 |
| A | 2014_2018 | MP | 0.00965 | 0.00786 | 0.00179 |
| A | 2014_2018 | OTHER | 0.01470 | 0.00624 | 0.00846 |
| A | 2014_2018 | S | 0.04246 | 0.02286 | 0.01960 |
| A | 2014_2018 | SD | 0.03639 | 0.01953 | 0.01686 |
| A | 2014_2018 | V | 0.01229 | 0.01255 | -0.00025 |
| B | 2018_2022 | C | 0.01474 | 0.01251 | 0.00223 |
| B | 2018_2022 | KD | 0.00961 | 0.01177 | -0.00216 |
| B | 2018_2022 | L | 0.00807 | 0.00836 | -0.00028 |
| B | 2018_2022 | M | 0.01849 | 0.01931 | -0.00082 |
| B | 2018_2022 | MP | 0.00976 | 0.00912 | 0.00064 |
| B | 2018_2022 | OTHER | 0.01744 | 0.00737 | 0.01007 |
| B | 2018_2022 | S | 0.02556 | 0.03212 | -0.00656 |
| B | 2018_2022 | SD | 0.01813 | 0.01863 | -0.00050 |
| B | 2018_2022 | V | 0.01752 | 0.01303 | 0.00449 |
| B0 | 2018_2022 | C | 0.01771 | 0.01251 | 0.00519 |
| B0 | 2018_2022 | KD | 0.00973 | 0.01177 | -0.00204 |
| B0 | 2018_2022 | L | 0.00830 | 0.00836 | -0.00006 |
| B0 | 2018_2022 | M | 0.01976 | 0.01931 | 0.00045 |
| B0 | 2018_2022 | MP | 0.00976 | 0.00912 | 0.00065 |
| B0 | 2018_2022 | OTHER | 0.01629 | 0.00737 | 0.00892 |
| B0 | 2018_2022 | S | 0.02596 | 0.03212 | -0.00616 |
| B0 | 2018_2022 | SD | 0.01993 | 0.01863 | 0.00130 |
| B0 | 2018_2022 | V | 0.02260 | 0.01303 | 0.00957 |

## Geographic robustness

| Test | Counties won | Winning vote coverage | Median county ΔMAE |
|---|---:|---:|---:|
| A | 0/21 | 0.0% | 0.00531 |
| B | 6/21 | 13.0% | 0.00054 |
| B0 | 4/21 | 9.8% | 0.00066 |

## Uncertainty

Districts are not independent. The intervals below therefore include a
municipality-cluster bootstrap as the more defensible diagnostic. They quantify
sampling variation within one realized election, not uncertainty over future
elections.

| Test | Bootstrap unit | Observed ΔMAE | 95% interval |
|---|---|---:|---:|
| A | district | 0.00510 | [0.00494, 0.00526] |
| A | municipality | 0.00510 | [0.00423, 0.00592] |
| B | district | 0.00079 | [0.00068, 0.00090] |
| B | municipality | 0.00079 | [0.00055, 0.00105] |
| B0 | district | 0.00198 | [0.00186, 0.00210] |
| B0 | municipality | 0.00198 | [0.00144, 0.00247] |

## Ablations

- A: previous party shares only
- B: A plus political structure
- C: B plus prior turnout and electorate size
- D: C plus strictly lagged historical sensitivity
- E: D plus municipality/county identifiers
- M2 frozen: previous party shares, turnout, entropy and raw electorate size

Historical sensitivity for a target transition uses only the immediately prior
transition and is unavailable where no high-quality identity chain exists.
It could therefore be computed for 2018→2022, but not for its 2014→2018
training rows; D/E were not estimable without importing the MEDIUM 2010→2014
mapping. Geography is never part of the primary model.

## Block experiment

Deferred pending the direct-party temporal result. Milestone 2 showed that
54.5–72.2% of residual energy remains within coalition blocks, so a bloc-first
model is not allowed to replace the party model without temporal evidence.

## Failure diagnostics

| Test | Scope | Model MAE | Baseline MAE | ΔMAE | Parties improved | OTHER ΔMAE |
|---|---|---:|---:|---:|---:|---:|
| A | all_parties | 0.01975 | 0.01465 | 0.00510 | 3/9 | 0.00846 |
| A | excluding_other | 0.02039 | 0.01571 | 0.00468 | 3/8 | 0.00846 |
| B | all_parties | 0.01548 | 0.01469 | 0.00079 | 5/9 | 0.01007 |
| B | excluding_other | 0.01524 | 0.01561 | -0.00037 | 5/8 | 0.01007 |
| B0 | all_parties | 0.01667 | 0.01469 | 0.00198 | 3/9 | 0.00892 |
| B0 | excluding_other | 0.01672 | 0.01561 | 0.00111 | 3/8 | 0.00892 |

`OTHER` is compositionally unstable across elections because its underlying
minor-party mix changes. Excluding it is diagnostic only and cannot change the
official all-party gate. The feature/residual correlation output shows whether
even simple local relationships preserve sign across transitions.

## Conclusion

**FAIL**

In strict B0, LightGBM reaches
1.667 pp versus
1.469 pp for proportional swing:
ΔMAE +0.198 pp. It wins
4/21 counties,
covering 9.8% of evaluation
votes.

The decision is based only on the frozen Milestone 2 LightGBM specification in
strict test B0 against proportional swing on a wholly unseen future election,
plus county robustness and municipality-bootstrap uncertainty. This gate
concerns temporal allocation of known national swing; it does not validate
polling, RICH demographics or a 2026 forecast.
