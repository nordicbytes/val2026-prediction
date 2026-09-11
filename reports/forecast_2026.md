# Produktionsprognos riksdagsvalet 2026

Cutoff: **2026-09-11 22:29 CEST**.
Modell: `T1_no_point_shrinkage_raked` (Milestone 4B, SUPPORTED). Ingen 4C. Ingen milestone 3-struktur.

Endast `forecast_snapshots/official_forecast_2026.json` bär officiell status, och filen får aldrig skrivas över. Den skrivs i två steg: först commit:as kontrakt, kod, input-lås och rapporter, därefter körs `valforecast forecast-2026 --official` så att snapshotens `git_commit` pekar på produktionskoden. Det tidigare timestampade utkastet, som pekade på HEAD före produktionskoden, är borttaget.

## Officiell snapshot

| Fält | Värde |
|---|---|
| Fil | `forecast_snapshots/official_forecast_2026.json` |
| SHA-256 | `48702a6de161f9605c833f4ae1337697d7ffee2fb8207fd57af12d084c9da6f7` |
| Skriven | 2026-09-11T21:26:34.995071+00:00 |
| `git_commit` | `a46bb2bc5a5b8eaacb39e658e2de8796ac4c9e8d` |
| `contract_sha256` | `11b248e06ff0eb7ecd92ad2ce7c9458e5cf483d17bc0173ed48915151c1360a4` |
| `input_lock_sha256` | `660634e35cee2bd1f10e151ce54acf199d4dafff6a3361cd84110a2192f7640d` |
| `source_manifest_hash` | `2362f165518a4bb730679d403efd7a1f16b48b384503f3747a95baeb7d449005` |
| Frö / drag | 20260911 / 2 000 |

Snapshoten innehåller 6 312 distrikt samt kommun-, läns- och valkretsaggregat. Ett nytt anrop av `--official` avbryts med fel; det är verifierat.

## Nationell punkt (all-verifiable productionstarget)

Vikten är röstberättigade 2026-08-14. Inga mandat. Productionstarget är senaste verifierade mätning per institut: Verian final, Novus/TV4 final, Ipsos final, Indikator final och Demoskop final.

Intervallen är en pollster-heterogenitetsapproximation. De fångar institutvariation via bootstrap plus nominal sampling inom varje mätning. De är **inte** ett valresultatintervall: de saknar historisk poll-mot-val-avvikelse, sena opinionsrörelser och valdeltagandeosäkerhet. Punkten är exakt lika med den låsta aggregatorn.

| Parti | Punkt | Låg | Hög |
|---|---:|---:|---:|
| S | 28,3 | 27,4 | 29,5 |
| SD | 18,7 | 17,9 | 19,6 |
| M | 17,1 | 16,0 | 18,2 |
| C | 8,1 | 7,4 | 8,9 |
| V | 7,4 | 6,8 | 8,1 |
| MP | 6,8 | 5,9 | 7,6 |
| KD | 6,7 | 5,9 | 7,5 |
| L | 5,1 | 4,6 | 5,6 |
| OTHER | 1,8 | 1,3 | 2,3 |

Liberalernas intervall ligger över fyraprocentspärren i huvudprognosen.

## Polltarget

Primary efter cutoff, verifieringsstege och `latest_fieldwork_end_per_pollster`:

- Verian/SVT 4–10 september
- Novus/TV4 7–10 september
- DN/Ipsos 7–10 september (Omni-tabell, OTHER 1,6 som DERIVED_RESIDUAL)
- Indikator/Ekot 2–10 september (Expressen-tabell)
- Demoskop 7–11 september (Placera/Direkt-tabell, OTHER 1,5 som DERIVED_RESIDUAL)

Äldre släpp från samma institut finns i inventeringen men väljs bort. House effects används inte. SCB Vid10 2026M05 rakar bara kärnan.

## Strikt-primär sensitivitet

Samma modell och samma kernel, men bara de tre pollster-hostade filerna: Verian final, Novus/TV4 final och Demoskop tidiga primärdiagram.

| Parti | Punkt | Låg | Hög |
|---|---:|---:|---:|
| S | 27,9 | 26,7 | 29,2 |
| SD | 18,9 | 17,8 | 20,4 |
| M | 16,8 | 15,4 | 18,3 |
| C | 8,7 | 7,8 | 9,6 |
| V | 7,3 | 6,4 | 8,2 |
| MP | 7,2 | 6,1 | 8,2 |
| KD | 6,3 | 5,4 | 7,8 |
| L | 4,8 | 2,9 | 5,6 |
| OTHER | 2,1 | 1,3 | 2,7 |

I den här varianten kan Liberalernas intervall gå under spärren. Det beror på att bootstrapen kan ge extra vikt åt det tidiga Demoskop-diagrammet, där L står på 2,8. Det är institutvariation, inte ett nytt valresultat.

Skillnaden mellan spåren är i huvudsak att sensitiviteten ersätter tre slutmätningar med ett elva dagar äldre Demoskop-diagram. Huvudspåret är det som speglar opinionen närmast cutoff.

## Geografi

6 312 distrikt. Officiell mappning: 4 937 SAME, 87 COMPARABLE, 35 MERGED, 1 253 UNMATCHED med explicit kommun- eller nationsfallback. MERGED kräver alla listade föregångare; delmängd accepteras inte. Giltiga röster summeras per unikt distrikt, inte per partirad.

## Begränsningar

DN-originalet är fortfarande paywallat. Ipsos-talet kommer från Omni:s namngivna tabell och korskontrolleras mot Expressen. Demoskops slutvektor kommer från Placera/Direkt och korskontrolleras mot Aftonbladet och Omni. Indikator.org bekräftar metod och fältperiod men inte en pinningsbar bildtabell. Sentio saknas fortfarande om bara aggregator finns. Offentlig kovarians saknas. Valdeltagande 2026 är okänt.
