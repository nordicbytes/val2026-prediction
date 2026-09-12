# Mandatberäkning för riksdagen

Forskningsberäkning. Den rör inte produktionskontraktet, den officiella
snapshoten eller `config/sources.yaml`. Webbsidan läser bara
`site/data/seats.json`.

Källor som faktiskt används är registrerade i
`config/sources_calibration.yaml`. Röster per valdistrikt 2022 kommer från
den redan pinnade filen i `config/sources.yaml`.

## Vad som är källbelagt och vad som är beräknat

Källbelagt:

- Reglerna i 3 kap. 6–7 §§ regeringsformen och 4 kap. 3 § samt 14 kap. 3–6 §§
  vallagen (2005:837), med Valmyndighetens manual VAL V785 05 som
  räkneexempel.
- De 310 fasta mandaten per valkrets 2022 **och 2026**, ur Valmyndighetens
  Excel *Mandat i respektive valkrets och valområde i valen 2022 och 2026*.
  2026-kolumnen är beslutet 2026-05-11 enligt 4 kap. 3 § vallagen, på
  röstberättigade den 1 mars 2026. Det är den fördelning som gäller på
  valdagen och indata till 2026-simuleringen.
- Facit för partiernas mandat 2022, ur Valmyndighetens beslut med bilagor:
  S 107, SD 73, M 68, V 24, C 24, KD 19, MP 18, L 16.
- Punktprognosen per valkrets och röstberättigade per valkrets 2026-08-14,
  ur `forecast_snapshots/official_forecast_2026.json`.
- Valdagsfelets kovarians, ur
  `reports/poll_calibration/estimator_lock.json`
  (`production_overlay_covariance.sigma`).

Beräknat:

- En Hamilton-kontroll av de 310 på snapshotens röstberättigade
  2026-08-14. Det är inte indata, bara en dokumenterad känslighetskontroll
  mot det redan fattade beslutet.
- Mandat per parti i punktprognosen och i 20 000 overlay-dragningar.
- Koalitionssannolikheter och andelen dragningar under fyraprocentspärren.

## Metoden, steg för steg

Riksdagen har **349 mandat = 310 fasta valkretsmandat + 39 utjämningsmandat**.

### 1. Fasta mandat per valkrets

Lagen fördelar de 310 fasta mandaten på de 29 valkretsarna efter antalet
röstberättigade den **1 mars valåret**, med största restmetoden
(Hamilton). Valmyndigheten beslutar talen senast **30 april**. För 2026
är det beslutet fattat och publicerat den 11 maj 2026. **Simuleringen
använder den officiella kolumnen**, inte en egen omräkning.

Hamilton-regeln, som Valmyndigheten tillämpat, är: varje valkrets får
först `floor(röstberättigade × 310 / rikets röstberättigade)` mandat.
Återstående mandat går till de största resterna. Vid lika rest avgör
lotten; i vår egen kontrollberäkning vinner den lägsta valkretskodens.
Inget lika överskott inträffade i den kontrollen.

En Hamilton-beräkning på röstberättigade **2026-08-14** finns kvar som
känslighetskontroll. Den skiljer sig från beslutet i exakt fyra
valkretsar, ett mandat var. Se tabellen nedan. Den beräkningen är inte
indata.

### 2. Spärrar

Ett parti deltar i fördelningen om det fått minst 4 procent i hela riket.
Ett parti under 4 procent får ändå delta i de fasta mandaten i en valkrets
där det fått minst 12 procent, men bara där. Partiet och dess mandat
bortses ifrån vid utjämningen.

### 3. Jämkade uddatalsmetoden

Jämförelsetalet är röstetalet delat med 1,2 innan partiet fått något mandat,
därefter delat med 3, 5, 7 och så vidare. Mandaten delas ut ett i taget till
högsta jämförelsetal. Vid lika jämförelsetal vinner det
lexikografiskt första partikodet. Det är en deterministisk ersättning för
lottning. Inget lika jämförelsetal inträffade i 2022-valideringen.

### 4. Fasta mandat mellan partier

De fasta mandaten i varje valkrets fördelas med jämkade uddatalsmetoden på
valkretsens röstetal, bland de partier som får delta där.

### 5. Totalfördelning och utjämning

349 mandat fördelas i hela riket mellan partier över 4 procent, med jämkade
uddatalsmetoden på rikets röstetal. Ett partis utjämningsmandat är
totalfördelningen minus dess fasta mandat.

Två undantag, iterativt:

1. Parti under 4 procent med fasta mandat via tolvprocentsregeln sätts åt
   sidan med sina mandat. Återstående mandat fördelas mellan övriga.
2. Om ett parti redan har minst så många fasta mandat som den aktuella
   totalfördelningen ger det, sätts partiet åt sidan med sina fasta mandat.
   De återstående mandaten fördelas på nytt mellan de andra. En ny
   omfördelning kan göra ytterligare ett parti överrepresenterat, så steget
   upprepas. Om flera partier är överrepresenterade samtidigt sätts det mest
   överrepresenterade åt sidan först (flest överskjutande mandat, därefter
   partikod).

I 2022 behövdes inget av undantagen. I 2026-punktprognosen, med
Valmyndighetens officiella fasta mandat, var både C och KD
överrepresenterade efter de fasta mandaten (30 respektive 24 fasta, noll
utjämning). De sattes åt sidan i den ordningen, och de återstående
mandaten fördelades mellan S, SD, M, V, MP och L.

### Vad som hoppas över, och varför

Vallagen 14 kap. 6 § fördelar utjämningsmandaten ut på valkretsar. Det
steget påverkar vilka ledamöter som sitter var, inte hur många mandat
varje parti får i riket. Vi behöver bara det senare, så steget är inte
implementerat.

Valmyndighetens manual beskriver också återföring av överskjutande fasta
mandat inne i enskilda valkretsar. Det steget ändrar vilken valkrets som
håller ett mandat. Nationella partitotaler vid överrepresentation följer
här 14 kap. 5 §: partiet behåller sina fasta mandat och sätts åt sidan.
I 2022 var inget parti överrepresenterat, så återföring och 14 kap. 5 §
ger samma nationella facit.

## Validering mot 2022

Röster summerades per valkrets och parti ur
`data/raw/valmyndigheten/2022/roster_per_distrikt_slutligt_riksdag.xlsx`
via `read_val2022_district_results()`. De åtta namngivna partiernas
nationella röstetal stämmer exakt mot Valmyndigheten:

| Parti | Röster |
|---|---:|
| S | 1 964 474 |
| SD | 1 330 325 |
| M | 1 237 428 |
| V | 437 050 |
| C | 434 945 |
| KD | 345 712 |
| MP | 329 242 |
| L | 298 542 |

OTHER i vår kanoniska kod blev 100 252 mot officiella 100 076 för övriga
anmälda partier. Differensen är 176 röster och påverkar inte
mandatfördelningen: OTHER är under 4 procent och tog inga mandat. De åtta
partierna är väl över spärren.

Fasta mandat per valkrets 2022 lästes ur Valmyndighetens Excel, inte
räknades fram ur valdagens röstberättigade (1 mars och valdagen skiljer
sig). Summan är 310.

Beräkningen ger **exakt** Valmyndighetens facit:

| Parti | Fasta | Utjämning | Totalt | Facit |
|---|---:|---:|---:|---:|
| S | 104 | 3 | 107 | 107 |
| SD | 69 | 4 | 73 | 73 |
| M | 67 | 1 | 68 | 68 |
| V | 16 | 8 | 24 | 24 |
| C | 23 | 1 | 24 | 24 |
| KD | 13 | 6 | 19 | 19 |
| MP | 10 | 8 | 18 | 18 |
| L | 8 | 8 | 16 | 16 |
| Summa | 310 | 39 | 349 | 349 |

Inget parti använde tolvprocentsregeln. Inget parti var
överrepresenterat efter de fasta mandaten. Inget tal justerades för att
träffa facit.

Som rimlighetskontroll, inte som indata: de fasta mandaten per valkrets
är överallt högst lika stora som Valmyndighetens totala mandat 2022
(fasta plus utjämning). Differensen summerar till 39. Vi fördelar inte
utjämningsmandat på valkrets, så den listan kan inte reproduceras som
utdata.

Ett enhetstest återger Valmyndighetens handräknade exempel 3
(första fyra mandaten S, S, M, C) och exempel 2 för Hamilton.

## Fasta mandat 2026

**Indata är Valmyndighetens officiella beslut** (kolumnen *Fasta mandat
2026* i den pinnade Excel-filen). Det är källbelagt, inte beräknat.

Kolumnen *Beräknat* nedan är vår egen Hamilton-kontroll på
röstberättigade **2026-08-14**. Differensen är augusti minus officiellt.
Den kolumnen används inte i `seats.json`.

| Valkrets | Kod | Röstberättigade 14 aug | Beräknat | Officiellt 1 mars | Diff |
|---|---|---:|---:|---:|---:|
| Stockholms kommun | 01 | 764 889 | 30 | 29 | +1 |
| Stockholms län | 02 | 1 067 172 | 41 | 41 | 0 |
| Uppsala län | 03 | 309 333 | 12 | 12 | 0 |
| Södermanlands län | 04 | 229 568 | 9 | 9 | 0 |
| Östergötlands län | 05 | 367 060 | 14 | 14 | 0 |
| Jönköpings län | 06 | 279 005 | 11 | 11 | 0 |
| Kronobergs län | 07 | 151 650 | 6 | 6 | 0 |
| Kalmar län | 08 | 191 083 | 7 | 7 | 0 |
| Gotlands län | 09 | 48 435 | 2 | 2 | 0 |
| Blekinge län | 10 | 123 298 | 5 | 5 | 0 |
| Malmö kommun | 11 | 267 820 | 10 | 10 | 0 |
| Skåne läns västra | 12 | 246 130 | 10 | 9 | +1 |
| Skåne läns södra | 13 | 313 023 | 12 | 12 | 0 |
| Skåne läns norra och östra | 14 | 248 742 | 10 | 10 | 0 |
| Hallands län | 15 | 268 009 | 10 | 10 | 0 |
| Göteborgs kommun | 16 | 462 708 | 18 | 18 | 0 |
| Västra Götalands läns västra | 17 | 297 167 | 11 | 11 | 0 |
| Västra Götalands läns norra | 18 | 210 138 | 8 | 8 | 0 |
| Västra Götalands läns södra | 19 | 174 136 | 7 | 7 | 0 |
| Västra Götalands läns östra | 20 | 210 051 | 8 | 8 | 0 |
| Värmlands län | 21 | 219 299 | 8 | 9 | −1 |
| Örebro län | 22 | 238 704 | 9 | 9 | 0 |
| Västmanlands län | 23 | 213 717 | 8 | 8 | 0 |
| Dalarnas län | 24 | 223 629 | 9 | 9 | 0 |
| Gävleborgs län | 25 | 222 290 | 9 | 9 | 0 |
| Västernorrlands län | 26 | 189 501 | 7 | 7 | 0 |
| Jämtlands län | 27 | 103 291 | 4 | 4 | 0 |
| Västerbottens län | 28 | 213 611 | 8 | 8 | 0 |
| Norrbottens län | 29 | 193 266 | 7 | 8 | −1 |
| Summa | | 8 046 725 | 310 | 310 | 0 |

Augustiberäkningen ger Stockholms kommun och Skåne läns västra ett mandat
var mer, och Värmland och Norrbotten ett mandat var mindre. Det är inte
försumbart, och det är därför simuleringen vilar på beslutet, inte på
kontrollen.

## 2026-simuleringen

### Indata och antaganden

1. Nationella dragningar är `run_forecast_2026(root).national_draws` med
   valdagsfel från `apply_overlay`. Sigma är
   `production_overlay_covariance.sigma`. Punkten är `national_point`.
   Frö `20260913`, tio overlay-replikater per basdragning, alltså
   20 000 dragningar. Det är medvetet samma dragningar som de
   publicerade dekomponerade sannolikheterna i
   `reports/poll_calibration/estimator_lock.json` under
   `probabilities_2026.sets.decomposed`. `overlay_probabilities`
   anropar `apply_overlay(..., seed=seed + 1)` för den uppsättningen,
   med tio fel-dragningar per basdragning. Mandatberäkningen använder
   därför `SEAT_OVERLAY_SEED = 20260913` och samma tio replikater, så
   att sidan inte kan säga en sak i sannolikhetsavsnittet och en annan
   i mandatavsnittet. Kontroll: `p_below_threshold` är komplementet
   till de publicerade `p_above` (L 0,1322 mot 0,8678, MP 0,0049 mot
   0,9951, KD 0,00185 mot 0,99815).
2. För varje dragning rakas valkretsmatrisen 29 × 9 mot den nationella
   vektorn med `calibrate_transition_matrix`, vikter =
   röstberättigade. Samma minimum-KL-rakning som produktionsmodellen
   använder mot distrikt, här på valkretsnivå.
3. Röstetal = andel × valkretsens röstberättigade. Det antar **likformigt
   valdeltagande mellan valkretsar**. Inom en valkrets spelar det ingen
   roll: uddatalsmetoden ser bara relativa röstetal. Mellan valkretsar
   påverkar det den nationella vektorn, men rakningen tvingar redan den
   vektorn att vara overlay-dragningen, så antagandet slår bara på hur
   rösterna fördelas geografiskt givet den nationella vektorn.
4. Mandatberäkningen körs på de röstetalen och på Valmyndighetens
   officiella 310 fasta mandat för 2026.

Inga valkretsdragningar är sparade. Osäkerheten är därför nationell, sedan
utlagd på valkretsarna med rakning. Det underskattar sannolikt
valkretsspecifik variation utöver den nationella.

### Punktprognosens mandat

Beräknat på den officiella snapshotens valkretsandelar, rakade mot
nationell punkt, med Valmyndighetens officiella fasta mandat.

| Parti | Fasta | Utjämning | Totalt |
|---|---:|---:|---:|
| S | 95 | 5 | 100 |
| SD | 63 | 3 | 66 |
| M | 55 | 6 | 61 |
| C | 30 | 0 | 30 |
| V | 19 | 7 | 26 |
| MP | 15 | 9 | 24 |
| KD | 24 | 0 | 24 |
| L | 9 | 9 | 18 |
| Summa | 310 | 39 | 349 |

C och KD tog fler fasta mandat än totalfördelningen. De behåller dem och
får inga utjämningsmandat. S+V+MP+C får 180 mandat i punkten. M+KD+L+SD
får 169.

### Median och 95-procentsintervall

Kvantilerna är närmaste heltal av 2,5-, 50- och 97,5-percentilerna över
20 000 heltalsdragningar. Medianerna behöver inte summera till 349.

| Parti | Punkt | Median | Låg | Hög |
|---|---:|---:|---:|---:|
| V | 26 | 27 | 21 | 32 |
| S | 100 | 101 | 91 | 111 |
| MP | 24 | 24 | 16 | 33 |
| C | 30 | 30 | 23 | 35 |
| L | 18 | 18 | 0 | 25 |
| M | 61 | 61 | 47 | 75 |
| KD | 24 | 24 | 18 | 30 |
| SD | 66 | 67 | 56 | 78 |

### Under fyraprocentspärren

Andel dragningar där partiets nationella andel, efter overlay, är under
4 procent. I de fallen får partiet noll mandat. Tolvprocentsregeln
utlöstes inte i punktprognosen.

| Parti | P(under 4 %) |
|---|---:|
| V | 0,0 % |
| S | 0,0 % |
| MP | 0,49 % |
| C | 0,0 % |
| L | 13,22 % |
| M | 0,0 % |
| KD | 0,185 % |
| SD | 0,0 % |

Overlay:n sätter den samlade restposten OTHER över 4 procent i 3,29
procent av dragningarna. OTHER är inte ett parti, utan många småpartier.
Inget av dem klarar spärren var för sig, så de rösterna behandlas som
spärrade. De 349 mandaten går till de namngivna partier som är över
4 procent. Det är inte en justering för att tvinga fram 349 hos de åtta;
det är den lagliga läsningen av en restpost.

### Koalitioner, sannolikhet för minst 175 mandat

S+V+MP+C och M+KD+L+SD partitionerar kammaren. 349 är udda, så exakt
en av de två har alltid majoritet.

| Konstellation | P(≥ 175) |
|---|---:|
| S+V+MP+C | 80,9 % |
| M+KD+L+SD | 19,1 % |
| S+V+MP+C+L | 98,4 % |
| S+C+MP+L | 37,6 % |
| S+M | 6,4 % |
| M+KD+SD | 1,6 % |
| S+V+MP | 0,05 % |
| M+KD+C+L | 0,0 % |
| S+C+L+KD+MP+M | 100 % |
| M+KD+L+SD+C | 99,95 % |

Det politiskt intressanta är inte den breda mitten, som alltid når 175,
utan att vänster-center har ungefär fyra femtedelar av dragningarna och
att L:s spärravgörande räcker för att flytta ungefär tio
procentenheter mellan blocken.

### När L ligger under spärren

L är under 4 procent i 2 644 av 20 000 dragningar (13,22 %). Då är L:s
mandat noll. Medelvärden när L är inne respektive ute:

| Parti | Medel när L inne | Medel när L ute | Differens |
|---|---:|---:|---:|
| L | 18,8 | 0,0 | −18,8 |
| M | 59,6 | 69,5 | +9,9 |
| SD | 66,3 | 69,7 | +3,4 |
| KD | 23,7 | 25,2 | +1,5 |
| MP | 24,0 | 26,0 | +2,0 |
| S | 100,9 | 101,7 | +0,8 |
| C | 29,3 | 29,5 | +0,2 |
| V | 26,4 | 27,3 | +0,9 |

M tar mer än hälften av L:s bortfall. Högersidan utan L
(M+KD+SD) plockar alltså upp mandat, men inte tillräckligt för att
ersätta L:s knappt 19. Majoritetsläget när L är ute mot när L är inne:

| Konstellation | P(≥ 175) när L inne | P(≥ 175) när L ute |
|---|---:|---:|
| S+V+MP+C | 79,6 % | 89,7 % |
| M+KD+L+SD | 20,4 % | 10,3 % |
| S+C+MP+L | 42,9 % | 2,6 % |
| S+M | 2,4 % | 32,9 % |
| S+V+MP+C+L | 99,8 % | 89,7 % |
| M+KD+SD | 0,2 % | 10,3 % |

När L åker ut vinner vänster-center oftare, mitten utan högern tappar
nästan all majoritet, och stor koalition S+M blir plötsligt tänkbar
därför att M sväller.

### Känslighet: augustiberäkningen mot det officiella beslutet

Samma 20 000 overlay-dragningar, samma frö, samma sigma. Enda skillnaden
är de fyra valkretsmandaten. Augustisiffrorna är inte indata; de visar
bara vad den felaktiga approximationen hade gjort.

Punktmandat som rörde sig:

| Parti | Officiellt beslut | Augustikontroll | Diff |
|---|---:|---:|---:|
| S | 100 | 101 | −1 |
| C | 30 | 29 | +1 |

Övriga sex partier oförändrade i punkten. Medianen rör sig likadant för
C (30 mot 29). M:s median är 61 i båda beräkningarna. Spärrandelarna är
identiska, som de måste vara: samma nationella dragningar. 95-procents-
intervallet för S är 91–111 officiellt mot 92–111 i augustikontrollen;
övriga intervall är identiska.

Koalitionssannolikheter som rörde sig:

| Konstellation | Officiellt | Augusti | Diff |
|---|---:|---:|---:|
| S+V+MP+C | 80,9 % | 80,6 % | +0,4 |
| M+KD+L+SD | 19,1 % | 19,4 % | −0,4 |
| S+C+MP+L | 37,6 % | 37,1 % | +0,5 |
| S+M | 6,4 % | 6,4 % | 0,0 |

Övriga konstellationer är oförändrade på den här upplösningen. Fyra
valkretsmandat räcker alltså för att flytta ett mandat från S till C i
punkten och ungefär fyra tiondels procentenhet mellan blocken. Det är
därför originalbeslutet används.

## Begränsningar

- Utjämningsmandat läggs inte ut på valkretsar.
- Likformigt valdeltagande mellan valkretsar.
- Ingen valkretsspecifik slump utöver den nationella overlay-dragningen.
- Inget material efter 2026-09-11 22:29 CEST. Inga 2026-valresultat. Ingen
  VALU.
- Overlay fångar historiskt valdagsfel givet instituten. Den fångar inte
  sena opinionsrörelser efter sista mätning eller okänt valdeltagande.
- Overlay behandlar OTHER som en partivektor. Det är en restpost. I 3,29
  procent av dragningarna går restposten över 4 procent; de rösterna
  ger inga mandat.
