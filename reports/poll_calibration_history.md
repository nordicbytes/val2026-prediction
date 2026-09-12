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

## Feldaterade rader, nu rättade

Korpusen hade ett dateringsfel. De engelska Wikipedia-sidorna för 2014, 2018
och 2022 listar hela mandatperiodens mätningar. I 2014-filen saknar
datumcellerna årtal — de innehåller bara `10–11 Sep` eller `9 Dec–7 Jan`.
Årtalet står i avdelarrader. Parsern defaultade till valåret. Varje rad från
tidigare år daterades därför till 2014. De flesta föll bort i 30-dagarsfönstret,
men rader vars dag och månad låg i augusti eller september överlevde och
hamnade i korpusen som valårsmätningar.

Jag verifierade avdelarna självständigt. De märker blocket ovanför sig:
rader före `'2014'` är 2014, före `'2013'` är 2013, och så vidare. Sista
blocket efter `'2011'` är 2010. Årsskifte hanteras som att fältslutets år är
blockets år, och att startdatumet backas när startmånaden är senare än
slutmånaden.

2018 och 2022 har redan årtal i datumcellerna. De var inte feldaterade.
2018 har två tabeller av samma längd (partier och koalitioner); parsern
tar partitabellen. 2022 har 26 tabeller; den stora nationella tabellen
har årtal i cellerna, resten är regionala och kommunala mätningar som
inte läses. TEMO-arkiven för 2002 och 2006 spänner över flera år, men
parsern läser redan årtalet ur raden och hoppar över andra år. De rördes
inte.

Konsekvensen i 2014: 22 av 45 rader i 30-dagarsfönstret hörde till 2013
(8), 2012 (7) eller 2011 (7). En av dem var en slutmätning.
Sifo/Verians "slutmätning" med fältslut 2014-09-13 och S=33,3 citerade
`web.archive.org/web/20121224073123/.../vb_sep_2012_svd.pdf` — Sifos
månadsmätning från september 2012. Den riktiga slutmätningen fanns
redan i korpusen: fält 10-11 sep, S=31,0, citerad till
`web/20140913214631/.../v37_2.pdf`. Valresultatet var S=31,0. Vi hade
alltså gett Sifo ett fel på +2,3 procentenheter där det rätta felet är
noll.

En arkivögonblicksbild kan inte vara äldre än mätningen den dokumenterar.
Före rättningen fanns 15 sådana omöjliga rader, alla i 2014, varav en
slutmätning. Bara 56 av 225 rader hade arkivcitat, så det var en undre
gräns. Efter rättningen är överträdelserna noll. Kontrollen ligger kvar
som test. En takkontroll på 80 rader per cykel i 30-dagarsfönstret ligger
också kvar: 2022 är den tätaste korrekt daterade cykeln (66), 2002 har
59, och 80 ligger över båda men under ett extra augusti/september-block.

Gold-cellerna, inklusive Sifo 2014-09-11 S=31,0, stämde efteråt utan att
ändras. Sample-size-overriderna i `config/poll_sample_sizes.yaml` pekar
fortfarande på rader som finns och som är rätt daterade. Ingen override
hade matchats mot en feldaterad rad.

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
| 2018 | 8 | 0,1345 | 0,1300 | ja |
| 2022 | 6 | 0,0955 | 0,0782 | ja |

Verdicten flippar inte. 2018-marginalen är 0,0045 i L1, ungefär 3,3
procent relativt. Den var 0,0016 före dateringsrättningen. 2022-marginalen
är också större (0,0173 mot 0,0139). Av de 28 slutmätningar som bidrar
till 2018-testet (20 träningsrader före 2018 och 8 utvärderingsrader
2018) är 26 overifierade nivå C, 93 procent. Träningen ensam är 18 av 20
overifierade. Verdicten ändras inte av det. Regeln var förregistrerad
och står.

### Leave-one-cycle-out, bara diagnostik

Det här får inte ändra verdicten. För varje cykel skattas house effects på
alla andra cykler och poängsätts på den hållna cykeln.

| Cykel | L1 utan | L1 med | Förbättring |
|---:|---:|---:|---|
| 2002 | 0,1165 | 0,1267 | nej |
| 2006 | 0,0653 | 0,0733 | nej |
| 2010 | 0,0641 | 0,0547 | ja |
| 2014 | 0,0971 | 0,0797 | ja |
| 2018 | 0,1345 | 0,1213 | ja |
| 2022 | 0,0955 | 0,0782 | ja |

House effects hjälper inte 2002 och 2006 när de hålls utanför. 2014:s
ojusterade L1 steg från 0,0847 till 0,0971 när den feldaterade
Sifo-slutmätningen försvann. 2018 blir starkare här än i gaten eftersom
2022 då får användas i träningen. Det är just därför det inte är gaten.

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
| 2014 | 2014-09-14 | 23 | 8 | Demoskop, Ipsos, Novus, SKOP, Sentio, Sifo/Verian, United Minds, YouGov | 0 | 0 | 23 |
| 2010 | 2010-09-19 | 7 | 4 | Ipsos, Novus, Sifo/Verian, United Minds | 0 | 0 | 7 |
| 2006 | 2006-09-17 | 20 | 4 | Demoskop, Ipsos, SKOP, Sifo/Verian | 6 | 0 | 14 |
| 2002 | 2002-09-15 | 59 | 4 | Demoskop, Ipsos, SKOP, Sifo/Verian | 18 | 0 | 41 |
| Summa | | 203 | 34 | | 24 | 0 | 179 |

2014 gick från 45 till 23 rader. 2018 och 2022 är oförändrade. 2022:s 66
rader är tätt men rätt daterade: Sifo, Novus och SKOP publicerade nästan
dagligen sista månaden.

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

Internkontroll av alla 203 rader: partisumman är 1, fältperioden slutar före
valdagen, och n ligger mellan 200 och 20 000 när n finns. Inga interna fel.

## Revision av nivå C

Urval: 15 nivå-C-rader, deterministiskt med seed 20260912. Urvalet byttes
när 22 feldaterade 2014-rader försvann. Varje rad jämfördes mot den
citerade A- eller B-källan, pinnad under `data/raw/polls/history/audit/`.

| Utfall | Antal |
|---|---:|
| Exakt mot A/B | 3 |
| Avvek | 0 |
| Overifierad | 12 |

Tre rader gick att kontrollera mot en samtida tabell eller PDF och stämde:
SKOP 2022-09-07, Demoskop 2022-08-21 och Sifo 2022-08-15. Tolv rader gick
inte att belägga. Nivå C duger inte som produktionsandel.

## House effects

Skattningen är sista mätningen per institutfamilj och cykel minus officiellt
resultat. Partiell pooling mot noll: krympning `n / (n + 3)`. Ett institut
med en cykel får därför bara en fjärdedel av sin råa skevhet.

A/B-verifierad betyder källnivå A eller B, eller en C-rad som revisionen
bekräftade. Overifierad C betyder övriga C-rader. Revisionsurvalet är 15
rader med fast seed; när 2014-raderna försvann byttes urvalet. Inizios
slutmätning 2018-09-07 bekräftades i förra revisionen men ingår inte i
det här urvalet. SKOP 2022-09-07 ingår och bekräftades.

| Institut | Cykler | Krympning | S, pp | A/B | C bekräftad | C overifierad | Bärare |
|---|---:|---:|---:|---:|---:|---:|---|
| Sifo/Verian | 6 | 0,67 | −0,61 | 0 | 0 | 6 | helt overifierad C |
| Ipsos | 6 | 0,67 | −1,29 | 2 | 0 | 4 | två A-rader (TEMO), resten C |
| Demoskop | 5 | 0,62 | −1,15 | 0 | 0 | 5 | helt overifierad C |
| SKOP | 5 | 0,62 | −1,25 | 0 | 1 | 4 | en bekräftad C-rad (2022) |
| Novus | 4 | 0,57 | −1,12 | 0 | 0 | 4 | helt overifierad C |
| Sentio | 3 | 0,50 | −2,38 | 0 | 0 | 3 | helt overifierad C |
| United Minds | 2 | 0,40 | −0,21 | 0 | 0 | 2 | helt overifierad C |
| YouGov | 2 | 0,40 | −1,20 | 0 | 0 | 2 | helt overifierad C |
| Inizio | 1 | 0,25 | −0,92 | 0 | 0 | 1 | overifierad C i det här urvalet |

Sifo/Verians S-effekt gick från −0,36 till −0,61. Det är den enda
house-effekten som rörde sig. Den feldaterade 2014-slutmätningen hade
S=33,3 mot valresultat 31,0, alltså +2,3. Den riktiga slutmätningen har
S=31,0 och fel noll. Att ta bort en plusavvikelse gör den skattade
S-underskattningen större, inte mindre. Övriga instituts S-effekter
står kvar. Sju av nio house effects vilar fortfarande helt på
overifierade C-rader. Ipsos har två egna TEMO-arkivrader. SKOP har en
revisionsbekräftad 2022-rad. Det ska stå så, även om gaten är SUPPORTED.

## Slutpollsfel, naiv kovarians

34 residualvektorer. Den naiva 9×9-kovariansen används bara som jämförelse.
Den dubbelräknar urvalsfel mot Dirichlet och behandlar en institutsnittad
2026-target som om den vore en enda mätning.

Spridning i procentenheter från den naiva kovariansen:

| Parti | SD, pp | Medelfel, pp |
|---|---:|---:|
| S | 1,71 | −2,03 |
| M | 1,88 | −0,63 |
| SD | 2,21 | −0,14 |
| L | 1,22 | +0,47 |
| C | 1,23 | −0,44 |
| KD | 1,17 | +0,19 |
| V | 0,97 | +1,28 |
| MP | 1,32 | +1,03 |
| OTHER | 1,37 | +0,28 |

S-underskattningen på omkring två procentenheter står sig. Medelfelet
gick från −1,97 till −2,03. Spridningen för S sjönk från 1,83 till 1,71
när den feldaterade Sifo-punkten försvann.

### Fel mot antal dagar före valet

| Dagar före valet | n | Medel L1 |
|---|---:|---:|
| 1-7 | 54 | 0,114 |
| 8-14 | 76 | 0,126 |
| 15-21 | 47 | 0,136 |
| 22-30 | 26 | 0,134 |

## Varianskomponenter

Residual för institut i i cykel c:

gemensam komponent för cykeln + institutkomponent + urvalskomponent.

Den gemensamma komponenten skattas från spridningen mellan de sex
cykelmedelvärdena, med Bessel-korrektion (dividera med C-1) och med
förväntat brus i de medlen bortdraget. Sex cykler är ett litet stickprov.
Den gemensamma variansen är därför osäker.

Institutkomponenten skattas från spridningen inom cykel.

Urvalskomponenten är analytisk: `deff * p * (1-p) / n` per parti, med
designeffekt 1. Inget annat är belagt. 31 av 34 slutmätningar har n.
Tre saknar fortfarande n och använder 1000, samma fallback som
produktionskontraktet.

De tre som saknar n är SKOP 2002-09-13, Novus 2014-09-06 och
Sifo/Verian 2014-09-11. SKOP 2002 har ingen n-kolumn i TEMO-arkivet
och NA i SwedishPolls. Novus 2014-PDF:en är i praktiken en bild.
Sifo-raden är nu den riktiga slutmätningen 10-11 sep; Wikipedia-cellen
har inget n, och jag har inte hittat på ett. Den feldaterade
2014-09-13-raden med S=33,3 är borta.

Urvalsvariansen dras bort från institutkomponenten. Golv vid noll.
Det träffar nu V och S. Tidigare bara V. Gemensam komponent golvades
inte.

Spridning i procentenheter efter uppdelningen:

Institutkolumnen är spridningen inom cykel efter att urvalsvariansen
dragits bort, före golvet. För V och S blev differensen negativ.
Overlayen nollställer diagonalen och projicerar sedan tillbaka till
en positiv semidefinit matris, så overlayvärdet kan bli strikt
positivt trots golvet. Siffrorna inom parentes är värdena före
dateringsrättningen.

| Parti | Gemensam | Institut, rå | Urval | Institut i overlay |
|---|---:|---:|---:|---|
| S | 1,30 (1,33) | −0,27 (0,86) | 1,21 (1,21) | golvad, 0,72 |
| M | 1,83 (1,69) | 0,43 (1,21) | 1,09 (1,09) | 0,62 |
| SD | 1,30 (1,42) | 1,55 (1,67) | 0,80 (0,79) | 1,59 |
| L | 0,89 (0,89) | 0,51 (0,59) | 0,69 (0,69) | 0,61 |
| C | 0,66 (0,63) | 0,72 (0,80) | 0,65 (0,65) | 0,77 |
| KD | 0,79 (0,78) | 0,27 (0,42) | 0,64 (0,64) | 0,35 |
| V | 0,59 (0,58) | −0,49 (0,39) | 0,72 (0,72) | golvad, 0,37 |
| MP | 0,95 (0,97) | 0,62 (0,66) | 0,66 (0,66) | 0,68 |
| OTHER | 1,16 (1,12) | 0,62 (0,91) | 0,47 (0,46) | 0,64 |

Komponenterna summerar fortfarande till mer än den totala
residualspridningen. För S ger de 1,92 procentenheter mot 1,71 i
totalen. Tidigare 1,99 mot 1,83.

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

Alla tre uppsättningarna utgår nu från samma bas: de nationella
dragningarna ur produktionsprognosen, alltså exakt den fördelning som de
publicerade officiella intervallen kommer ur. Tidigare lades overlayen på
pollmålens dragningar medan den officiella kolumnen kom från de
distriktsavstämda dragningarna. Skillnaden var liten, för S omkring 0,2
procentenheter, men tabellen jämförde två olika baser och gör det inte
längre. Varje basdragning replikeras tio gånger med oberoende valdagsfel,
så overlaykolumnerna vilar på 20 000 dragningar i stället för 2 000.

| Parti | Punkt | Officiell | Naiv overlay | Uppdelad overlay |
|---|---:|---|---|---|
| S | 28,34 | 27,35-29,48 | 24,81-31,93 | 25,39-31,23 |
| SD | 18,71 | 17,89-19,63 | 14,30-23,13 | 15,70-21,75 |
| M | 17,08 | 16,02-18,20 | 13,25-20,90 | 13,20-20,91 |
| C | 8,07 | 7,35-8,85 | 5,56-10,56 | 6,43-9,72 |
| V | 7,44 | 6,77-8,06 | 5,48-9,41 | 6,05-8,82 |
| MP | 6,81 | 5,92-7,63 | 4,12-9,48 | 4,65-8,95 |
| KD | 6,68 | 5,91-7,48 | 4,31-9,04 | 4,95-8,43 |
| L | 5,08 | 4,57-5,59 | 2,68-7,47 | 3,18-6,96 |
| OTHER | 1,78 | 1,34-2,28 | 0,00-4,41 | 0,00-4,12 |

Liberalerna mot fyraprocentspärren, punkten 5,08 i alla tre:

| Uppsättning | Intervall | Låg mot 4% |
|---|---|---:|
| Officiell | 4,57-5,59 | +0,57 pp |
| Naiv overlay | 2,68-7,47 | −1,32 pp |
| Uppdelad overlay | 3,18-6,96 | −0,82 pp |

Den officiella lågpunkten ligger över spärren. Båda overlayerna går under.
Den uppdelade är fortfarande smalare än den naiva. L är inte säkert över
spärren om intervallet ska vara ett prognosintervall mot valresultatet.
OTHER-lågpunkten i overlay kan nå noll. Det är en normalapproximation,
inte en föreslagen publicerad siffra.

## Sannolikheter

Ett intervall svarar inte på frågan hur troligt något är. Ur samma
dragningar räknas därför två sannolikheter: att vänster-mitten
S+V+C+MP får fler röster än M+SD+KD+L, och att varje parti når över
fyraprocentspärren.

| Uppsättning | P(S+V+C+MP störst) | P(L över 4%) | P(KD över 4%) | P(MP över 4%) |
|---|---:|---:|---:|---:|
| Officiell | 98,9 % | 100 % | 100 % | 100 % |
| Naiv overlay | 73,9 % | 81,3 % | 98,7 % | 98,0 % |
| Uppdelad overlay | 77,3 % | 86,8 % | 99,8 % | 99,5 % |

Bara den uppdelade raden är en sannolikhet om valresultatet. Den
officiella raden svarar på en annan fråga, nämligen hur eniga de fem
mätningarna är, och 98,9 % ska inte läsas som en valchans.

Monte Carlo-felet redovisas i låsfilen per sannolikhet. Det är 0,23
procentenheter på den officiella blockraden, som vilar på 2 000
dragningar, och 0,30-0,31 på overlayraderna, som vilar på 20 000. Siffrorna
bör därför inte anges med decimal i publik text.

Sannolikheterna gäller röstandelar, inte mandat. Om L faller under spärren
fördelas partiets mandat om till övriga, vilket slår hårdare mot
M+SD+KD+L än vad röstandelarna antyder. Vi räknar ingen mandatfördelning
och kan därför inte uttala oss om regeringsunderlag.

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
    resultat. 2018-marginalen är 0,0045. De flesta bärande raderna är
    overifierad nivå C. Partiell pooling mot noll med prior_strength=3.

uncertainty:
  poll_uncertainty: pollster_bootstrap_then_dirichlet
  election_day_error: common_plus_institute_over_kish_n_eff
  election_day_error_source: reports/poll_calibration/estimator_lock.json
```

Använd inte den naiva residualkovariansen. `poll_uncertainty` lämnas
oförändrad. Punktprognosen ska fortsätta vara aggregatorn.

## Luckor

Gamla primärkällor är svåra att hitta. Paywalls är vanliga. 2010 är tunt.
SCB saknas. Tre slutmätningar saknar fortfarande n: SKOP 2002-09-13,
Novus 2014-09-06 och Sifo/Verian 2014-09-11. Jag har inte hittat på de
n:en. Kalibreringskällor får inte återinföras i `config/sources.yaml`.
