# Metod för produktionsprognos 2026

## Modell

Den låsta kärnan är Milestone 4B-primary `T1_no_point_shrinkage_raked`. Regional 4C-konditionering är förbjuden. Milestone 3:s statiska lokala struktur återinförs inte. Mandat beräknas inte.

## Transitionskärna

T1 skattas från SCB PSU 2026M05 med samma metod som 4B: publicerade andelar oförändrade i punktskattningen, Dirichlet-drag med margin-inverterad approximativ n_eff, nollkrympning, stated-party-betingning. Varje drag rakas först till same-wave Vid10 2026M05. Valdagens nationella target är därefter den låsta pollaggregatorn, inte Vid10.

Nollandelar i 2022-vektorer får samma crumb som 4B: nollantal ersätts med 0,5 innan andelen återställs.

## Verifieringsstege

Kontraktet kräver inte längre att productionstalet kommer från en pollster-hostad primärfil. En mätning får ingå om den klarar en låst stege:

1. Pollster- eller beställaroriginal, eller
2. Komplett namngiven tabell i etablerad medie- eller nyhetsbyråpublicering, korskontrollerad mot pollster eller beställare för metod, fältperiod, n eller blocktal.

Aggregatorer utan sådan stege, till exempel Wikipedia eller Botten Ada, är uteslutna. Sentio är därför fortfarande utanför.

Siffror från snippets hårdkodas inte som enda bevis. Parsern läser pinnade HTML-sidor och bilder med checksumma. Gold-cell-tester låser de utlästa cellerna.

## Pollaggregation

House effects skattas inte. Det finns ingen låst historisk poll-mot-val-korpus i repot, och att skatta instituteffekter mot 2022 skulle kräva ny overifierad historisk ingest.

Aggregatorn är tids- och urvalsviktad:

`vikt = exp(-ln(2) * ålder_dagar / 7) * sqrt(n)`

Ålder räknas från fältperiodens slut till cutoff-datum.

### Pollsterbalans

Förregistrerad regel: `latest_fieldwork_end_per_pollster`.

1. Behåll verifierade kompletta vektorer före cutoff.
2. Kollapsa överlappande fältperioder från samma institut till den senaste.
3. Behåll därefter bara den senaste kvarvarande mätningen per institut (`fieldwork_end`, sedan `publication_date`).

Syftet är inte att optimera utfallet utan att ett institut inte ska få större vikt bara för fler släpp. Tidsdecay och `sqrt(n)` tillämpas på den enda kvarvarande mätningen. Productionstarget är därför Verian final, Novus/TV4 final, Ipsos final, Indikator final och Demoskop final.

Den tidigare strikt-hostade trepollsvarianten (Verian final, Novus/TV4 final, Demoskop tidiga diagram) är låst som sensitivitet. Den körs med samma kernel men egen pollaggregation.

### OTHER och residual

Saknad namngiven partikolumn ratas. OTHER får fyllas i automatiskt bara om de namngivna andelarna summerar till 1 ± 0,5 procentenheter.

`DERIVED_RESIDUAL` är en separat, dokumenterad härledning. Den tillåts bara när alla åtta namngivna partier är publicerade och den namngivna summan ligger i [0,97, 1,00]. Residualen är 1 minus den namngivna summan. Det är inte en fabricerad publicerad cell. Ipsos OTHER 1,6 och Demoskop final OTHER 1,5 är sådana residualer.

Därefter renormalisering. Offentlig kovarians saknas.

SCB Vid10 får inte styra valdagens target.

## Osäkerhet och avstämning

2 000 deterministiska drag, frö 20260911.

Den tidigare Dirichlet-vikten fångade bara nominal sampling inom varje mätning och underskattade institutvariation. Den förregistrerade ersättningen är pollster-bootstrap:

1. Punktskattningen är den vanliga viktade medelnivån, utan bootstrap.
2. För varje osäkerhetsdrag resamplas instituten med återläggning.
3. De fasta recency- och sqrt(n)-vikterna följer med de dragna mätningarna och renormaliseras inom bootstraputfallet. En mätning som dras två gånger behåller sin fasta vikt två gånger.
4. Därefter dras Dirichlet inom varje (eventuellt upprepad) mätning.

Det är en approximation av pollsterheterogenitet, inte ett valresultatintervall. Exakt nationell reconciliation mot respektive polltarget kvarstår.

Varje drag: transitionosäkerhet enligt 4B, pollosäkerhet enligt bootstrap plus Dirichlet, IPF av kärnan till polltarget, därefter IPF av distriktsmatrisen mot samma target med röstberättigade som vikt. Intervall är lika-svansade 95 procent av dragen.

Punktprognosen är inte medelvärdet av dragen. Dragmedelvärdet är ett medelvärde av bootstrapade target och sammanfaller inte med den deterministiska aggregatorn. Distriktspunkten rakas därför en sista gång mot den låsta punkttargeten, så att den röstberättigadeviktade nationella punkten är exakt lika med aggregatorn. Det testas.

Samma frö ger samma drag. Det testas.

## 2022→2026-universum

Måluniversumet är Valmyndighetens 2026-distrikt med röstberättigade vid kvalifikationsdagen 14 augusti 2026. Den officiella jämförelsen 2022–2026, publicerad 17 augusti 2026, används.

- `Kan jämföras` / SAME: en 2022-föregångare kopieras.
- `Kan jämföras` / COMPARABLE: en jämförbar föregångare kopieras.
- `Kan jämföras mot flera` / MERGED: röstviktat medel av **alla** listade 2022-föregångare. En tyst delmängd accepteras inte; saknade föregångare ger fallback plus diagnostik.
- `Ej jämförbart` / UNMATCHED: kommunens 2022-fördelning, annars nationella 2022.

Giltiga röster summeras per unikt distrikt, inte en gång per partirad. Förväntade relationer: 4 937 SAME, 87 COMPARABLE, 35 MERGED, 1 253 UNMATCHED. Vikten i nationell avstämning är 2026 års röstberättigade.

## Förbjudet

2026-valresultat, VALU och exit polls, material efter cutoff, 4C-regionalisering, milestone 3-struktur, efterhandsjustering och sekundära aggregatorsiffror som produktionstal.

## Snapshot och lås

`valforecast lock-forecast-2026-inputs` låser kontrakt, källmanifest, inkluderade och exkluderade pollråfiler, båda targetvarianterna samt övriga kärnfiler. `load_input_lock` jämför också `poll_sha256` och pollråfilernas checksummor.

Snapshoten bär `git_commit`, `source_manifest_hash`, `contract_sha256`, `input_lock_sha256`, `random_seed`, `n_draws` och en explicit `uncertainty.interpretation` som säger att intervallet är en pollsterheterogenitetsapproximation, inte ett valresultatintervall.

`valforecast forecast-2026 --dry-run` skriver ingen fil. `--official` skapar bara `forecast_snapshots/official_forecast_2026.json` och vägrar skrivning om filen redan finns. Timestamp-snapshots är utkast tills `git_commit` pekar på den commit som innehåller kontrakt, kod och input-lås. Parent commit:ar först, därefter körs den officiella snapshoten.
