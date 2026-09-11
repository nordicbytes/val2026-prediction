# Milestone 4C — Regional transition availability

This note is written before election scoring. It records what public
pre-election data can support regional conditioning.

## Three-way table

No public 2018 or 2022 PSU wave publishes

```text
previous_party × current_party × region
```

`Rostningssympati1900` is national only. VALU and election-study matrices are
retrospective and remain blocked. Restricted microdata were not available to a
public forecaster at either May cutoff.

4C therefore does **not** estimate an observed regional joint. It keeps the
locked national 4B kernel `T1_no_point_shrinkage_raked` and calibrates
region-specific copies to published regional current-vote margins.

## What is available

| Cycle | Object | Vintage | Published | Geography | Contents |
|---|---|---|---|---|---|
| 2018 | Regional val-idag | Original news 2018-06-05 | 2018-06-05 | 8 Vid12 groups | percent and ± |
| 2022 | Regional val-idag | Original news 2022-06-02 | 2022-06-02 | 8 Vid12 groups | percent and ±; OTHER suppressed in three regions |
| both | National previous × current | Locked 4A/4B sources | May reports | National | percent, ±, row bases |
| both | Previous-election composition | Valmyndigheten | before each forecast | District → 8 groups | official votes |

The original 2018 PDF Table 3 is **partisympati**, not val-idag, and uses a
10-group weighting geography. It is not a 4C primary source. Live PxWeb
`Vid12` `2018M05` is the post-2020 revised series and is forbidden for the
2018 holdout.

## Region codebook

The locked 8-group map is county-nested except Stockholm municipality:

- `0180` Stockholm municipality
- `SE01exkl0180` rest of Stockholms län
- `SE12` Östra Mellansverige
- `SE09` Småland med öarna
- `SE0A` Västsverige, including Göteborg
- `SE2` Sydsverige, including Malmö
- `SE06` Norra Mellansverige
- `SE07+SE08` Mellersta och övre Norrland

Districts do not cross municipality or county lines, so the map is exact at
municipality grain. No GIS reconstruction is required.

## Pooling

A previous-party-and-region hierarchical prior would need vintage-correct
historical regional val-idag waves for both holdouts. Original 2015–2017 news
tables are not ingested here, and live historical `Vid12` would mix the 2020
revision into the 2018 hierarchy. Secondary pooling is therefore omitted and
cannot be promoted after scoring.

## Estimand

For region `r`:

```text
previous_r × T_r ≈ reconciled regional val-idag
```

where `T_r` starts from the locked national 4B matrix and `previous_r` is the
full-Sweden previous-election vote vector in that region. National Vid10
enters only as a reconciliation constraint, never as a row prior.
