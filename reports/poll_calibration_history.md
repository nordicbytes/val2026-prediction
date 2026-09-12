# Historisk pollkalibrering

Det här är en kalibreringskorpus, inte en produktionsinput. Den används för att
skatta house effects och en empirisk fördelning över hur mycket slutmätningar
missar valresultatet. Den får inte läsas som andelar i 2026-aggregatet.

Produktionskontraktet förbjuder fortfarande sekundära aggregatorsiffror som
produktionsandelar. Nivå C är tillåten bara här, och bara märkt som
kalibrering. En rad i den här korpusen är inte ett 2026-poll.

Kalibreringskällorna ligger i `config/sources_calibration.yaml`.
`config/sources.yaml` är produktionsmanifestet. Dess sha256 är
`2362f165518a4bb730679d403efd7a1f16b48b384503f3747a95baeb7d449005` och
matchar `source_manifest_hash` i den officiella snapshoten.
`reports/forecast_2026/input_lock.json` är det committade låset. Att lägga
kalibreringsrader i produktionsmanifestet bryter verifieringskedjan.

Officiell snapshot
`forecast_snapshots/official_forecast_2026.json` är orörd. Punktprognosen är
orörd. `config/forecast_2026.yaml` är orörd. Milestone 4A, 4B och 4C är orörda.
Ingen 2026-valresultatdata, ingen VALU och inget material efter cutoff
2026-09-11 22:29 CEST har använts.

Kommando: `uv run valforecast calibrate-poll-history`.
Lås: `reports/poll_calibration/estimator_lock.json`.

## Förregistrerad gate

Regeln skrevs ner innan utfallen lästes, och den ändrades inte efteråt.

För varje T i {2018, 2022}: skatta house effects på slutmätningar i cykler med
valår strikt före T, med partiell pooling mot noll och `prior_strength=3`. I
cykel T är den ojusterade prognosen det lika viktade medelvärdet av
slutmätningarna. Den justerade prognosen drar bort den krympta house-effekten
(noll om institutet inte setts). Utfallsmåttet är L1-avståndet mot det
officiella nationella resultatet. Verdict är SUPPORTED bara om den justerade
prognosen har strikt lägre L1 i både 2018 och 2022.

Om house effects inte håller ska produktionskontraktet fortsätta utan dem.

## Verdict

**SUPPORTED**

| T | Slutmätningar | L1 utan house | L1 med house | Förbättring |
|---|---:|---:|---:|---|
| 2018 | 8 | 0,1345 | 0,1329 | ja |
| 2022 | 6 | 0,0955 | 0,0816 | ja |

2018-marginalen är 0,0016 i L1, ungefär 1,2 procent relativt. Av de 28
slutmätningar som bidrar till 2018-testet (20 träningsrader före 2018 och
8 utvärderingsrader 2018) är 25 overifierade nivå C, 89 procent. Träningen
ensam är 18 av 20 overifierade. Verdicten ändras inte av det. Regeln var
förregistrerad och står.

### Leave-one-cycle-out, bara diagnostik

Det här får inte ändra verdicten. För varje cykel skattas house effects på
alla andra cykler och poängsätts på den hållna cykeln.

| Cykel | L1 utan | L1 med | Förbättring |
|---:|---:|---:|---|
| 2002 | 0,1165 | 0,1255 | nej |
| 2006 | 0,0653 | 0,0754 | nej |
| 2010 | 0,0641 | 0,0513 | ja |
| 2014 | 0,0847 | 0,0727 | ja |
| 2018 | 0,1345 | 0,1237 | ja |
| 2022 | 0,0955 | 0,0816 | ja |

House effects hjälper inte 2002 och 2006 när de hålls utanför. 2018 blir
starkare här än i gaten eftersom 2022 då får användas i träningen. Det är
just därför det inte är gaten.

## Korpus

Parsern läser pinnade filer under `data/raw/polls/history/`. Inga andelar är
hårdkodade från sökresultat. Officiella resultat 2010-2022 kommer från
Valmyndighetens redan pinnade distriktsfiler, inklusive valdistrikt som är
samlingsdistrikt. 2006 och 2002 kommer från Valmyndighetens resultatsida som
redovisar båda åren.

Fönstret är fältperiod som slutar 1-30 dagar före valdagen. Vallokal-,
VALU- och valdagsrader tas bort. Minimumet 2010, 2014, 2018 och 2022 finns.
2006 och 2002 är med som best effort.

TEMO:s eller Synovates egen arkivsida är nivå A för TEMO/Synovate (här
Ipsos-familjen). När samma sida redovisar andra institut är den en
tredjepartssammanställning, alltså nivå C, inte en etablerad medietabell.
De raderna är omklassade. Nivå B är tom i den här korpusen.

| Cykel | Valdag | Rader | Slutmätningar | Institut | A | B | C |
|---:|---|---:|---:|---|---:|---:|---:|
| 2022 | 2022-09-11 | 66 | 6 | Demoskop, Ipsos, Novus, SKOP, Sentio, Sifo/Verian | 0 | 0 | 66 |
| 2018 | 2018-09-09 | 28 | 8 | Demoskop, Inizio, Ipsos, Novus, SKOP, Sentio, Sifo/Verian, YouGov | 0 | 0 | 28 |
| 2014 | 2014-09-14 | 45 | 8 | Demoskop, Ipsos, Novus, SKOP, Sentio, Sifo/Verian, United Minds, YouGov | 0 | 0 | 45 |
| 2010 | 2010-09-19 | 7 | 4 | Ipsos, Novus, Sifo/Verian, United Minds | 0 | 0 | 7 |
| 2006 | 2006-09-17 | 20 | 4 | Demoskop, Ipsos, SKOP, Sifo/Verian | 6 | 0 | 14 |
| 2002 | 2002-09-15 | 59 | 4 | Demoskop, Ipsos, SKOP, Sifo/Verian | 18 | 0 | 41 |
| Summa | | 225 | 34 | | 24 | 0 | 201 |

Det här är inte en komplett historik. Engelska Wikipedia täcker 2014-2022.
Svenska Wikipedia används för 2010, och 30-dagarsfönstret där är gles.
SCB PSU saknas i fönstret. Inizio syns först 2018. YouGov syns 2014 och
2018. SD särredovisas inte i de officiella 2002- och 2006-tabellerna, så
OTHER inkluderar SD de åren och house effects för SD hoppar över dem.

Svenska Wikipedia-sidor för 2006, 2014, 2018 och 2022 är nedladdade men inte
inkopplade i parsern. Engelska sidor för 2010, 2006 och 2002 gav 404.

### Gold-celler

Resultat, nationellt:

| År | Cell | Andel |
|---:|---|---:|
| 2022 | S / SD / L | 30,33 / 20,54 / 4,61 |
| 2018 | S | 28,26 |
| 2014 | S | 31,01 |
| 2010 | S | 30,66 |
| 2006 | S / M | 34,99 / 26,23 |
| 2002 | S / M | 39,85 / 15,26 |

Mätningar, en rad per cykel:

| År | Institut | Fält slut | S |
|---:|---|---|---:|
| 2022 | Sifo/Verian | 2022-09-07 | 29,5 |
| 2018 | Inizio | 2018-09-07 | 24,6 |
| 2014 | Sifo/Verian | 2014-09-11 | 31,0 |
| 2010 | Sifo/Verian | 2010-09-16 | 30,3 |
| 2006 | Sifo/Verian | 2006-09-11 | 37,0 |
| 2002 | Sifo/Verian | 2002-09-12 | 37,1 |

2006-raden är omnormerad. I TEMO-arkivet summerade namngivna partier plus
OTHER till 97,6. Parsern kräver en vektor nära 1 och skalar då om. Det är
dokumenterat, inte tyst.

Internkontroll av alla 225 rader: partisumman är 1, fältperioden slutar före
valdagen, och n ligger mellan 200 och 20 000 när n finns. Inga interna fel.

## Revision av nivå C

Urval: 15 nivå-C-rader, deterministiskt med seed 20260912. Varje rad
jämfördes mot den citerade A- eller B-källan, pinnad under
`data/raw/polls/history/audit/`.

| Utfall | Antal |
|---|---:|
| Exakt mot A/B | 5 |
| Avvek | 0 |
| Overifierad | 10 |

De fem som gick att kontrollera mot en samtida tabell eller PDF stämde.
Tio rader gick inte att belägga. Flera 2014-länkar pekar på fel år. Nivå C
duger inte som produktionsandel.

## House effects

Skattningen är sista mätningen per institutfamilj och cykel minus officiellt
resultat. Partiell pooling mot noll: krympning `n / (n + 3)`. Ett institut
med en cykel får därför bara en fjärdedel av sin råa skevhet.

A/B-verifierad betyder källnivå A eller B, eller en C-rad som revisionen
bekräftade. Overifierad C betyder övriga C-rader.

| Institut | Cykler | Krympning | S, pp | A/B | C bekräftad | C overifierad | Bärare |
|---|---:|---:|---:|---:|---:|---:|---|
| Sifo/Verian | 6 | 0,67 | −0,36 | 0 | 0 | 6 | helt overifierad C |
| Ipsos | 6 | 0,67 | −1,29 | 2 | 0 | 4 | två A-rader (TEMO), resten C |
| Demoskop | 5 | 0,62 | −1,15 | 0 | 0 | 5 | helt overifierad C |
| SKOP | 5 | 0,62 | −1,25 | 0 | 0 | 5 | helt overifierad C |
| Novus | 4 | 0,57 | −1,12 | 0 | 0 | 4 | helt overifierad C |
| Sentio | 3 | 0,50 | −2,38 | 0 | 0 | 3 | helt overifierad C |
| United Minds | 2 | 0,40 | −0,21 | 0 | 0 | 2 | helt overifierad C |
| YouGov | 2 | 0,40 | −1,20 | 0 | 0 | 2 | helt overifierad C |
| Inizio | 1 | 0,25 | −0,92 | 0 | 1 | 0 | en bekräftad C-rad |

Sju av nio house effects vilar helt på overifierade C-rader. Ipsos har två
egna TEMO-arkivrader. Inizio har en revisionsbekräftad PDF. Det ska stå
så, även om gaten är SUPPORTED.

## Slutpollsfel, naiv kovarians

34 residualvektorer. Den naiva 9×9-kovariansen används bara som jämförelse.
Den dubbelräknar urvalsfel mot Dirichlet och behandlar en institutsnittad
2026-target som om den vore en enda mätning.

Spridning i procentenheter från den naiva kovariansen:

| Parti | SD, pp | Medelfel, pp |
|---|---:|---:|
| S | 1,83 | −1,97 |
| M | 2,09 | −0,41 |
| SD | 2,35 | −0,23 |
| L | 1,24 | +0,47 |
| C | 1,25 | −0,49 |
| KD | 1,20 | +0,14 |
| V | 1,01 | +1,26 |
| MP | 1,35 | +1,05 |
| OTHER | 1,50 | +0,18 |

### Fel mot antal dagar före valet

| Dagar före valet | n | Medel L1 |
|---|---:|---:|
| 1-7 | 60 | 0,126 |
| 8-14 | 82 | 0,131 |
| 15-21 | 51 | 0,148 |
| 22-30 | 32 | 0,149 |

## Varianskomponenter

Residual för institut i i cykel c:

gemensam komponent för cykeln + institutkomponent + urvalskomponent.

Den gemensamma komponenten skattas från spridningen mellan de sex
cykelmedelvärdena, med Bessel-korrektion (dividera med C-1) och med
förväntat brus i de medlen bortdraget. Sex cykler är ett litet stickprov.
Den gemensamma variansen är därför osäker.

Institutkomponenten skattas från spridningen inom cykel.

Urvalskomponenten är analytisk: `deff * p * (1-p) / n` per parti, med
designeffekt 1. Inget annat är belagt. 30 av 34 slutmätningar saknar n.
Då används 1000, samma fallback som produktionskontraktet. Om de sanna n
är större drar vi bort för mycket urvalsvarians.

Urvalsvariansen dras bort från institutkomponenten. Golv vid noll. Det
träffade V, S och KD: inom-cykel-spridningen var mindre än den förväntade
urvalsvariansen, så institutkomponenten golvades till noll för de partierna.
Den gemensamma komponenten golvades inte.

Spridning i procentenheter efter uppdelningen:

Institutkolumnen är den råa spridningen inom cykel, före att urvalsvariansen
dras bort. För V, S och KD blev differensen negativ, så deras institutbidrag
är golvat till noll i overlayen trots att tabellen visar ett rått värde.

| Parti | Gemensam | Institut, rå | Urval | Institut i overlay |
|---|---:|---:|---:|---|
| S | 1,30 | 0,85 | 1,41 | golvad till 0 |
| M | 1,68 | 1,08 | 1,27 | 1,08 |
| SD | 1,42 | 1,62 | 0,92 | 1,62 |
| L | 0,89 | 0,51 | 0,80 | 0,51 |
| C | 0,63 | 0,73 | 0,76 | 0,73 |
| KD | 0,78 | 0,29 | 0,74 | golvad till 0 |
| V | 0,57 | 0,40 | 0,84 | golvad till 0 |
| MP | 0,97 | 0,58 | 0,77 | 0,58 |
| OTHER | 1,12 | 0,88 | 0,53 | 0,88 |

Komponenterna summerar till mer än den totala residualspridningen. För S ger
de 2,10 procentenheter mot 1,83 i totalen. Orsaken är n-fallbacken: med
n=1000 för 30 av 34 slutmätningar överskattas urvalsvariansen, eftersom
verkliga slutmätningar oftast har 1 500-3 000 svarande. Konsekvensen är att
institutkomponenten över-subtraheras och att den uppdelade overlayen därför
snarare är något för smal än för bred. Att fylla i verkliga n för de 34
slutmätningarna är den enskilt viktigaste förbättringen av den här
skattningen.

2026-komponenten är hela den gemensamma kovariansen plus
institutkovariansen delad med effektivt antal institut:

`n_eff = 1 / sum_i w_i^2`

`Sigma_2026 = Sigma_common + Sigma_institute / n_eff`

w_i är produktionens normaliserade vikter (recency och sqrt(n) på de fem
inkluderade instituten). Här är n_eff 4,95. Urvalskomponenten läggs inte
tillbaka. Dirichlet täcker den redan.

Den försvarbara overlayen är den uppdelade. Den naiva är för bred eftersom
den både dubbelräknar urvalsfel och låter institutfelet vara lika stort som
för en ensam slutmätning.

## 2026-intervall, tre uppsättningar

Ingen ny snapshot. Punkten är densamma i alla tre.

| Parti | Punkt | Officiell | Naiv overlay | Uppdelad overlay |
|---|---:|---|---|---|
| S | 28,34 | 27,35-29,48 | 24,63-32,12 | 25,50-31,17 |
| SD | 18,71 | 17,89-19,63 | 14,16-23,22 | 15,50-21,78 |
| M | 17,08 | 16,02-18,20 | 12,95-21,17 | 13,65-20,68 |
| C | 8,07 | 7,35-8,85 | 5,48-10,64 | 6,48-9,60 |
| V | 7,44 | 6,77-8,06 | 5,31-9,46 | 6,04-8,82 |
| MP | 6,81 | 5,92-7,63 | 4,10-9,54 | 4,63-8,99 |
| KD | 6,68 | 5,91-7,48 | 4,29-9,16 | 5,00-8,29 |
| L | 5,08 | 4,57-5,59 | 2,69-7,58 | 3,25-6,98 |
| OTHER | 1,78 | 1,34-2,28 | −0,02-4,65 | 0,00-4,03 |

Liberalerna mot fyraprocentspärren, punkten 5,08 i alla tre:

| Uppsättning | Intervall | Låg mot 4% |
|---|---|---:|
| Officiell | 4,57-5,59 | +0,57 pp |
| Naiv overlay | 2,69-7,58 | −1,31 pp |
| Uppdelad overlay | 3,25-6,98 | −0,75 pp |

Den officiella lågpunkten ligger över spärren. Båda overlayerna går under.
Den uppdelade är smalare än den naiva, men den säger fortfarande att L inte
är säkert över spärren om intervallet ska vara ett prognosintervall mot
valresultatet. OTHER-lågpunkten i overlay kan nå noll. Det är en
normalapproximation, inte en föreslagen publicerad siffra.

## Föreslagen kontraktsdiff

`config/forecast_2026.yaml` ska inte ändras i den här omgången. Hashen i
`tests/test_forecast_2026_freeze.py` är
`11b248e06ff0eb7ecd92ad2ce7c9458e5cf483d17bc0173ed48915151c1360a4`.
Om detta senare ska aktiveras är det här den exakta diffen. Inget av detta
är applicerat.

```yaml
poll_aggregation:
  house_effects: empirical_partial_pool_if_gate_supported
  house_effects_source: reports/poll_calibration/estimator_lock.json
  house_effects_reason: >
    Gate SUPPORTED för T=2018 och T=2022 på L1 mot officiellt nationellt
    resultat. 2018-marginalen är 0,0016. De flesta bärande raderna är
    overifierad nivå C. Partiell pooling mot noll med prior_strength=3.

uncertainty:
  poll_uncertainty: pollster_bootstrap_then_dirichlet
  election_day_error: common_plus_institute_over_kish_n_eff
  election_day_error_source: reports/poll_calibration/estimator_lock.json
```

Använd inte den naiva residualkovariansen. `poll_uncertainty` lämnas
oförändrad. Punktprognosen ska fortsätta vara aggregatorn.

## Luckor

Gamla primärkällor är svåra att hitta. Paywalls är vanliga. Wikipedia 2014
återanvänder href:er från fel år. 2010 är tunt. SCB saknas. De flesta
slutmätningarna saknar n. Jag har inte hittat på rader som saknar pin.
Kalibreringskällor får inte återinföras i `config/sources.yaml`.
