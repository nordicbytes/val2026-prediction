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
target is used in scaling, imputation, encoding or fitting. LightGBM with
ablation C is preregistered as the primary model because it won Milestone 2;
Ridge and ElasticNet are comparators. B0 (official 2014→2018 only, tested on
2018→2022) is the strict primary gate. A and pooled B use the non-official
MEDIUM stable-ID 2010→2014 mapping and are sensitivity tests.

| Train | Test | Ablation | Model | Weighted MAE | Baseline | ΔMAE |
|---|---|---|---|---:|---:|---:|
| 2014_2018 | 2018_2022 | A_previous_shares | ridge | 0.01691 | 0.01469 | 0.00222 |
| 2014_2018 | 2018_2022 | A_previous_shares | elastic_net | 0.01661 | 0.01469 | 0.00191 |
| 2014_2018 | 2018_2022 | A_previous_shares | lightgbm | 0.01665 | 0.01469 | 0.00195 |
| 2014_2018 | 2018_2022 | B_plus_structure | ridge | 0.01703 | 0.01469 | 0.00234 |
| 2014_2018 | 2018_2022 | B_plus_structure | elastic_net | 0.01668 | 0.01469 | 0.00199 |
| 2014_2018 | 2018_2022 | B_plus_structure | lightgbm | 0.01677 | 0.01469 | 0.00208 |
| 2014_2018 | 2018_2022 | C_plus_turnout_size | ridge | 0.01705 | 0.01469 | 0.00236 |
| 2014_2018 | 2018_2022 | C_plus_turnout_size | elastic_net | 0.01675 | 0.01469 | 0.00206 |
| 2014_2018 | 2018_2022 | C_plus_turnout_size | lightgbm | 0.01668 | 0.01469 | 0.00199 |
| 2010_2014 | 2014_2018 | A_previous_shares | ridge | 0.02246 | 0.01465 | 0.00780 |
| 2010_2014 | 2014_2018 | A_previous_shares | elastic_net | 0.02223 | 0.01465 | 0.00758 |
| 2010_2014 | 2014_2018 | A_previous_shares | lightgbm | 0.01988 | 0.01465 | 0.00522 |
| 2010_2014 | 2014_2018 | B_plus_structure | ridge | 0.02261 | 0.01465 | 0.00795 |
| 2010_2014 | 2014_2018 | B_plus_structure | elastic_net | 0.02231 | 0.01465 | 0.00765 |
| 2010_2014 | 2014_2018 | B_plus_structure | lightgbm | 0.01981 | 0.01465 | 0.00516 |
| 2010_2014 | 2014_2018 | C_plus_turnout_size | ridge | 0.02208 | 0.01465 | 0.00743 |
| 2010_2014 | 2014_2018 | C_plus_turnout_size | elastic_net | 0.02177 | 0.01465 | 0.00712 |
| 2010_2014 | 2014_2018 | C_plus_turnout_size | lightgbm | 0.01974 | 0.01465 | 0.00509 |
| 2010_2014 + 2014_2018 | 2018_2022 | A_previous_shares | ridge | 0.01658 | 0.01469 | 0.00189 |
| 2010_2014 + 2014_2018 | 2018_2022 | A_previous_shares | elastic_net | 0.01620 | 0.01469 | 0.00150 |
| 2010_2014 + 2014_2018 | 2018_2022 | A_previous_shares | lightgbm | 0.01561 | 0.01469 | 0.00092 |
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
| A | 2014_2018 | C | 0.01966 | 0.01919 | 0.00047 |
| A | 2014_2018 | KD | 0.01358 | 0.01419 | -0.00061 |
| A | 2014_2018 | L | 0.00929 | 0.00845 | 0.00084 |
| A | 2014_2018 | M | 0.01990 | 0.02101 | -0.00111 |
| A | 2014_2018 | MP | 0.00971 | 0.00786 | 0.00185 |
| A | 2014_2018 | OTHER | 0.01464 | 0.00624 | 0.00839 |
| A | 2014_2018 | S | 0.04233 | 0.02286 | 0.01947 |
| A | 2014_2018 | SD | 0.03628 | 0.01953 | 0.01675 |
| A | 2014_2018 | V | 0.01228 | 0.01255 | -0.00027 |
| B | 2018_2022 | C | 0.01479 | 0.01251 | 0.00228 |
| B | 2018_2022 | KD | 0.00968 | 0.01177 | -0.00209 |
| B | 2018_2022 | L | 0.00811 | 0.00836 | -0.00024 |
| B | 2018_2022 | M | 0.01847 | 0.01931 | -0.00084 |
| B | 2018_2022 | MP | 0.00980 | 0.00912 | 0.00068 |
| B | 2018_2022 | OTHER | 0.01772 | 0.00737 | 0.01035 |
| B | 2018_2022 | S | 0.02567 | 0.03212 | -0.00646 |
| B | 2018_2022 | SD | 0.01817 | 0.01863 | -0.00046 |
| B | 2018_2022 | V | 0.01759 | 0.01303 | 0.00456 |
| B0 | 2018_2022 | C | 0.01761 | 0.01251 | 0.00509 |
| B0 | 2018_2022 | KD | 0.00975 | 0.01177 | -0.00202 |
| B0 | 2018_2022 | L | 0.00824 | 0.00836 | -0.00012 |
| B0 | 2018_2022 | M | 0.01958 | 0.01931 | 0.00027 |
| B0 | 2018_2022 | MP | 0.00981 | 0.00912 | 0.00069 |
| B0 | 2018_2022 | OTHER | 0.01670 | 0.00737 | 0.00933 |
| B0 | 2018_2022 | S | 0.02609 | 0.03212 | -0.00603 |
| B0 | 2018_2022 | SD | 0.01964 | 0.01863 | 0.00101 |
| B0 | 2018_2022 | V | 0.02274 | 0.01303 | 0.00971 |

## Geographic robustness

| Test | Counties won | Winning vote coverage | Median county ΔMAE |
|---|---:|---:|---:|
| A | 0/21 | 0.0% | 0.00542 |
| B | 6/21 | 13.0% | 0.00037 |
| B0 | 5/21 | 10.7% | 0.00070 |

## Uncertainty

Districts are not independent. The intervals below therefore include a
municipality-cluster bootstrap as the more defensible diagnostic. They quantify
sampling variation within one realized election, not uncertainty over future
elections.

| Test | Bootstrap unit | Observed ΔMAE | 95% interval |
|---|---|---:|---:|
| A | district | 0.00509 | [0.00493, 0.00524] |
| A | municipality | 0.00509 | [0.00420, 0.00592] |
| B | district | 0.00086 | [0.00076, 0.00098] |
| B | municipality | 0.00086 | [0.00063, 0.00111] |
| B0 | district | 0.00199 | [0.00187, 0.00212] |
| B0 | municipality | 0.00199 | [0.00147, 0.00248] |

## Ablations

- A: previous party shares only
- B: A plus political structure
- C: B plus prior turnout and electorate size
- D: C plus strictly lagged historical sensitivity
- E: D plus municipality/county identifiers

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
| A | all_parties | 0.01974 | 0.01465 | 0.00509 | 3/9 | 0.00839 |
| A | excluding_other | 0.02038 | 0.01571 | 0.00467 | 3/8 | 0.00839 |
| B | all_parties | 0.01556 | 0.01469 | 0.00086 | 5/9 | 0.01035 |
| B | excluding_other | 0.01529 | 0.01561 | -0.00032 | 5/8 | 0.01035 |
| B0 | all_parties | 0.01668 | 0.01469 | 0.00199 | 3/9 | 0.00933 |
| B0 | excluding_other | 0.01668 | 0.01561 | 0.00108 | 3/8 | 0.00933 |

`OTHER` is compositionally unstable across elections because its underlying
minor-party mix changes. Excluding it is diagnostic only and cannot change the
official all-party gate. The feature/residual correlation output shows whether
even simple local relationships preserve sign across transitions.

## Conclusion

**FAIL**

In strict B0, LightGBM reaches
1.668 pp versus
1.469 pp for proportional swing:
ΔMAE +0.199 pp. It wins
5/21 counties,
covering 10.7% of evaluation
votes.

The decision is based only on preregistered LightGBM CORE ablation C in strict
test B0 against proportional swing on a wholly unseen future election, plus
county robustness. This gate concerns temporal allocation of known national
swing; it does not validate polling, RICH demographics or a 2026 forecast.
