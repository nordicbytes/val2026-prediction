# Data audit: initial 2018→2022 milestone

## Scope

This audit uses official final Riksdag results and the official Valmyndigheten
physical-district comparison. Collection districts are retained in canonical
election data and national totals but are not treated as physical districts.

## District coverage

- 2018 result identifiers: 6325
- 2022 result identifiers including collection districts:
  6578
- Canonical valid-vote total 2018: 6476725
- Canonical valid-vote total 2022: 6477970
- Physical districts in official 2022 comparison:
  6264
- Directly comparable physical districts: 4153
- Comparable merged districts: 11
- Districts in evaluation: 4164
- Excluded/problematic physical districts: 2100
- Evaluation share of national valid votes:
  64.86%

The official `Jämförbart` field uses `ja`, `nej`, one predecessor code, or
multiple predecessor codes. SAME and COMPARABLE rows use one 2018 district.
MERGED rows sum predecessor vote counts before shares are calculated.
`NOT_COMPARABLE` rows are excluded; no split is inferred from this file.

## Quality checks

- 2018 districts with logged issues: 321
- 2022 identifiers with logged issues: 314

The detailed rows are stored under `data/interim`. Expected collection-district
exceptions (votes but zero registered voters) remain logged rather than hidden.

## Baseline results

All errors are vote-share fractions; multiply by 100 for percentage points.

| Model | District weighted MAE | District unweighted MAE | National MAE |
|---|---:|---:|---:|
| previous_result | 0.01955 | 0.01996 | 0.01337 |
| uniform_national_residual | 0.01556 | 0.01633 | 0.00068 |
| uniform_national_swing | 0.01554 | 0.01631 | 0.00069 |
| proportional_swing | 0.01469 | 0.01541 | 0.00107 |

This is a structural benchmark using the *realized* 2022 national swing, not a
pre-election polling backtest. It isolates the first question: how much local
error remains after applying one common national movement?

## Gate conclusion

**UNCLEAR.** The proportional swing benchmark improves on no-change, but no
model using pre-election local features has been tested yet. This result cannot
establish predictive local signal beyond national swing.
