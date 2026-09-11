# Pollinventering inför prognos 2026

Cutoff: **2026-09-11 22:29 CEST**. Inventeringen gör inte anspråk på fullständighet.
Siffror i aggregatorn kommer från pinnade artefakter med checksumma, inte från snippets som enda bevis.

## Productionstarget (all-verifiable)

Senaste verifierade mätning per institut enligt verifieringsstegen.

| Poll | Pollster | Beställare | Publicerad | Fältperiod | n | Klass | OTHER |
|---|---|---|---|---|---:|---|---|
| verian_svt_2026_sep_final | Verian | SVT | 2026-09-11 | 4–10 sep | 3 063 | static_primary_document | publicerad |
| novus_tv4_2026_09_11 | Novus | TV4 | 2026-09-11 14:59 | 7–10 sep | 2 292 | static_primary_document | publicerad |
| dn_ipsos_2026_09_11 | Ipsos | DN | 2026-09-11 15:38 | 7–10 sep | 1 800 | established_media_table_crosschecked | DERIVED_RESIDUAL 1,6 |
| indikator_ekot_2026_09_11 | Indikator Opinion | Ekot | 2026-09-11 08:20 | 2–10 sep | 2 189 | established_media_table_crosschecked | publicerad 1,6 |
| demoskop_2026_09_11 | Demoskop | Aftonbladet/SvD | 2026-09-11 15:13 | 7–11 sep | 1 419 | established_media_table_crosschecked | DERIVED_RESIDUAL 1,5 |

Novus n är det värde TV4-parsern läser ur den pinnade NEXT_DATA-sidan.

## Strikt-primär sensitivitet

Låst trepollsvariant. Samma hostade filer som den tidigare huvudprognosen.

| Poll | Pollster | Beställare | Publicerad | Fältperiod | n | Källa |
|---|---|---|---|---|---:|---|
| verian_svt_2026_sep_final | Verian | SVT | 2026-09-11 | 4–10 sep | 3 063 | Primär PDF |
| novus_tv4_2026_09_11 | Novus | TV4 | 2026-09-11 14:59 | 7–10 sep | 2 292 | Primär HTML |
| demoskop_2026_sep_early | Demoskop | Aftonbladet/SvD | 2026-09-03 | 25 aug–1 sep | 1 998 | Primärdiagram |

## Alla parsade mätningar

| Poll | Ingår i production | Ingår i strict | Skäl om nej |
|---|---|---|---|
| verian_svt_2026_sep_monthly | nej | nej | pollster_balance_not_latest |
| verian_svt_2026_sep_final | ja | ja | |
| novus_monthly_2026_sep | nej | nej | pollster_balance_not_latest |
| novus_tv4_2026_09_09 | nej | nej | overlapping_same_pollster |
| novus_tv4_2026_09_11 | ja | ja | |
| demoskop_2026_sep_early | nej | ja | pollster_balance_not_latest i production |
| demoskop_2026_09_11 | ja | nej | inte i den låsta hostade sensitiviteten |
| dn_ipsos_2026_09_11 | ja | nej | inte hostad primärfil |
| indikator_ekot_2026_09_11 | ja | nej | inte hostad primärfil |
| indikator_ekot_2026_09_11_omni | nej | nej | secondary_source (Expressen är productionstabellen) |

### Verifierade partivektorer (procent, före residualrenorm)

- Verian 3 sep: V 8,1; S 29,2; MP 8,0; C 7,4; L 3,0; M 16,4; KD 6,3; SD 18,9; OTHER 2,8
- Verian 11 sep: V 7,7; S 27,6; MP 8,0; C 8,2; L 5,5; M 15,8; KD 6,5; SD 18,1; OTHER 2,7
- Novus månad: V 8,4; S 25,8; MP 7,8; C 8,3; L 3,3; M 17,2; KD 6,5; SD 20,7; OTHER 2,0
- Novus/TV4 7–10 sep: V 6,6; S 28,6; MP 6,4; C 9,2; L 4,7; M 18,1; KD 5,5; SD 19,5; OTHER 1,4
- Demoskop 25 aug–1 sep (Bild1): V 7,9; S 27,2; MP 6,9; C 8,6; L 2,8; M 16,6; KD 7,9; SD 20,0; OTHER 2,1
- DN/Ipsos 7–10 sep: V 7,6; S 27,4; MP 6,8; C 7,6; L 5,0; M 18,3; KD 6,9; SD 18,8; OTHER 1,6 (DERIVED_RESIDUAL)
- Indikator/Ekot 2–10 sep: V 7,8; S 28,6; MP 6,9; C 7,5; L 5,4; M 16,8; KD 7,1; SD 18,3; OTHER 1,6
- Demoskop 7–11 sep: V 7,5; S 29,7; MP 5,6; C 7,7; L 4,7; KD 7,6; M 16,7; SD 19,0; OTHER 1,5 (DERIVED_RESIDUAL)

## Källbevis för de nya slutvektorerna

### DN/Ipsos

DN:s originalartikel är pinnad men paywallad. Den öppna HTML:en innehåller inte de åtta namngivna andelarna, så de läses inte därifrån.

Omni-sidan är den pinnade tabellartefakten: S 27,4; M 18,3; SD 18,8; C 7,6; V 7,6; L 5,0; KD 6,9; MP 6,8. Namngiven summa 98,4. OTHER 1,6 är därför DERIVED_RESIDUAL, inte en publicerad cell.

Expressen 11 sep 15:38 korskontrollerar n=1 800, fält 7–10 september, L 5 procent och block 49,3 mot 49,0.

### Indikator/Ekot

Expressen publicerar den kompletta vektorn, inklusive Övriga 1,6, plus n=2 189 och fält 2–10 september.

Indikator.org/opinion-sr/ pinnas för metod och bekräftar 2189 samt 2026-09-02–2026-09-10. Sidan renderar JS-diagram utan pinningsbara 2026-bild-URL:er, så den är inte tabellkälla.

Omni har samma kompletta vektor och parsas i inventeringen, men exkluderas som secondary_source så att institutet inte dubbelräknas.

### Demoskop slutmätning

Placera/Direkt har septemberraden V 7,5; S 29,7; MP 5,6; C 7,7; L 4,7; KD 7,6; M 16,7; SD 19,0 och block 50,5 mot 48,0. Namngiven summa 98,5. OTHER 1,5 är DERIVED_RESIDUAL.

Aftonbladets originalkolumn korskontrollerar S 29,7, L 4,7 och opposition 50,5.

Omni korskontrollerar n=1 419 och fält 7–11 september. SvD-sidan är paywallad och används inte som tabell.

## Inte i någon target

| Källa | Status | Skäl |
|---|---|---|
| Sentio | Ingen verifierbar primär eller komplett medietabell | sentio.no hade ingen verifierbar svensk mätning i fönstret. Wikipedia/Botten Ada är aggregatorer. |
| SCB Vid10 2026M05 | Kernel only | Same-wave-raking, inte valdagens target. |

## SCB 2026

Rostningssympati1900 och Vid10 för `2026M05` hämtades från PxWeb. Gold cells oförändrade: S→S 75,8; M→M 62,0; SD→SD 74,9; hela väljarkåren S 26,8. Bastal 4 542.
Vid10: S 33,9; L 2,5; OTHER 2,0. Publicerad summa 99,8; residual läggs på OTHER enligt kontraktet.

## Luckor

Webben 2026 är fortfarande ofullständig. DN- och SvD-originalen är paywallade. Indikator.org saknar pinningsbar bildtabell. Sentio saknas. Det som nu finns är kompletta eller härledbara slutvektorer i pinnade medieartefakter, korskontrollerade mot beställare eller institutsida.
