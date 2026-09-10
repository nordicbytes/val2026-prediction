# Milestone 2: structural signal before SCB

## Fixed evaluation population

All models use the same 4,164 physical 2022 districts as milestone 1:
4,153 direct official comparisons plus 11 official merged comparisons.
Validation is five-fold municipality-group out-of-fold plus the official
21-fold leave-one-county-out (LOCO) protocol. Primary models use structural
features only; municipality and county identifiers are ablations. Training
weights use 2018 valid votes and evaluation weights use 2022 valid votes.

The model target is `actual_share - proportional_swing_prediction`. The
additive `actual_local_swing - national_percentage_point_swing` remains a
separate diagnostic target.

## Selection bias

Positive standardized differences mean higher values among included districts.
The 2018 political comparison for excluded districts is necessarily a
municipality-level proxy because `NOT_COMPARABLE` districts have no defensible
2018 district predecessor.

| Feature | Included mean | Excluded mean | Standardized difference |
|---|---:|---:|---:|
| municipality_share_MP | 0.0419 | 0.0480 | -0.332 |
| municipality_population_change | 0.0300 | 0.0386 | -0.315 |
| municipality_share_L | 0.0530 | 0.0586 | -0.300 |
| municipality_share_V | 0.0776 | 0.0874 | -0.288 |
| municipality_electorate_change | 0.0355 | 0.0415 | -0.270 |
| municipality_share_OTHER | 0.0149 | 0.0158 | -0.256 |
| municipality_share_C | 0.0873 | 0.0820 | 0.240 |
| municipality_share_SD | 0.1795 | 0.1668 | 0.234 |

| SKR 2017 main group | District inclusion | Valid-vote inclusion |
|---|---:|---:|
| Mindre orter och landsbygd | 81.0% | 80.5% |
| Storstäder och storstadsnära | 61.4% | 63.6% |
| Större städer och närkommuner | 61.8% | 61.7% |

`municipality_electorate_change` is change in registered eligible voters.
`municipality_population_change` uses SCB's official 2017-12-31 and
2021-12-31 municipal population totals. The time-correct SKR 2017 kommuntyp is
reported separately.

The maximum absolute standardized difference is 0.332. Excluded
districts are concentrated in faster-growing and more metropolitan
municipalities. The frozen 1.469 pp benchmark therefore describes the
officially comparable subset and should not be generalized to all districts.

## Residual structure

- Districts: 4164
- First weighted PCA component: 51.35%
- Second weighted PCA component: 18.18%
- Largest residual standard deviation: S
- Largest absolute PC1 loading: S
- Residual energy represented by equal-within-block 2022 coalition totals:
  27.79%
- Residual energy represented when allocated by prior within-block shares:
  45.53%

Party distributions, correlations, block residuals, size/profile/swing
breakdowns and diagnostic PNGs are in this directory.

PC1 must not be interpreted as a latent left-right factor: its dominant loading
is reported above, residuals have an exact sum-to-zero constraint that induces
negative correlations, and the represented-energy figures include OTHER as a
tautological singleton block. The unexplained within-coalition remainder is
72.21% under equal allocation and
54.47% under prior-share allocation. The primary
block diagnostic and model comparison use the 2022 S-led (V+S+MP+C) and
Tidö/right (L+M+KD+SD) grouping; the 2018
red-green/Alliance grouping is retained in separately labelled sensitivity
outputs.

## Ridge feature ablation

| Held-out group | Feature set | Weighted MAE | Improvement |
|---|---|---:|---:|
| municipality_id | previous_vote_shares | 0.01143 | 22.18% |
| municipality_id | previous_shares_plus_turnout_entropy_size | 0.01104 | 24.85% |
| municipality_id | previous_shares_plus_context_and_geography | 0.01127 | 23.28% |
| county_id | previous_vote_shares | 0.01179 | 19.75% |
| county_id | previous_shares_plus_turnout_entropy_size | 0.01131 | 23.01% |
| county_id | previous_shares_plus_context_and_geography | 0.01279 | 12.92% |

The previous vote shares alone beat proportional swing. The next ablation adds
only prior turnout, party entropy and electorate size; exact linear
combinations of party shares are excluded. Geography identifiers do not improve
the stricter held-out-county result.

## Out-of-fold model comparison

Errors are vote-share fractions. The frozen primary benchmark is the
proportional-swing row.

| Model | Held-out group | Weighted MAE | Absolute improvement | Relative improvement |
|---|---|---:|---:|---:|
| proportional_swing | municipality_id | 0.01469 | 0.00000 | 0.00% |
| ridge | municipality_id | 0.01104 | 0.00365 | 24.85% |
| elastic_net | municipality_id | 0.01105 | 0.00364 | 24.80% |
| lightgbm | municipality_id | 0.01064 | 0.00405 | 27.59% |
| catboost | municipality_id | 0.01066 | 0.00404 | 27.47% |
| proportional_swing | county_id | 0.01469 | 0.00000 | 0.00% |
| ridge | county_id | 0.01131 | 0.00338 | 23.01% |
| elastic_net | county_id | 0.01132 | 0.00337 | 22.92% |
| lightgbm | county_id | 0.01087 | 0.00382 | 26.00% |
| catboost | county_id | 0.01092 | 0.00377 | 25.68% |

An improvement counts as evidence only if it is positive out-of-fold against
the frozen benchmark. In-sample fit is not reported.

## LOCO acceptance gate

The gate requires lower structure-only MAE than 1.469 pp, wins in at least
11/21 counties covering at least 50% of evaluation votes, national aggregate
MAE at most 0.003, and no more than two parties worsened by over 0.0001.

| Model | Counties won | Winning vote coverage | National MAE | Worsened parties | Pass |
|---|---:|---:|---:|---:|---|
| catboost | 21/21 | 100.0% | 0.00048 | 0 | True |
| elastic_net | 21/21 | 100.0% | 0.00032 | 0 | True |
| lightgbm | 21/21 | 100.0% | 0.00036 | 1 | True |
| ridge | 21/21 | 100.0% | 0.00029 | 0 | True |
