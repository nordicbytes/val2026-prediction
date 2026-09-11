# Val 2026 prediction

Reproducerbart researchsystem för prognoser av svenska riksdagsval på
valdistriktsnivå.

Den första milstolpen är en strikt 2018→2022-backtest:

1. hämta och checksumma officiella råfiler,
2. normalisera slutliga riksdagsresultat,
3. använd Valmyndighetens officiella distriktsjämförelse,
4. jämför tidigare resultat med uniform nationell swing,
5. rapportera fel och datakvalitet.

Projektet modellerar geografiska populationer, inte individers väljarbeteende.
Rådata är versionslagrad och skrivs aldrig över implicit.

## Kom igång

```bash
uv sync
uv run valforecast sources validate
uv run valforecast sources fetch
uv run valforecast build-initial
uv run valforecast analyze-structure
uv run valforecast validate-temporally
uv run valforecast validate-transitions
uv run pytest
```

`analyze-structure` reproducerar selection-bias-diagnostik, residualplots och
struktur-only-utvärdering av Ridge, ElasticNet, LightGBM och CatBoost med
fem kommungrupper och 21-fold leave-one-county-out. Träning viktas med 2018
års giltiga röster och utvärdering med 2022 års giltiga röster.
På macOS kräver LightGBM systempaketet `libomp` (`brew install libomp`).

`validate-transitions` reproducerar Milestone 4A: SCB:s ursprungliga
väljarmatriser från maj 2018 och maj 2022 kalibreras till respektive
samtida nationella PSU-läge och jämförs med proportionell swing. Kommandot
stannar vid T0-gaten och bygger inte MRP.

Produktionsprognosen 2026 låses och körs separat:

```bash
uv run valforecast lock-forecast-2026-inputs
uv run valforecast forecast-2026 --dry-run
uv run valforecast forecast-2026 --official
```

Kommandona använder den låsta nationella 4B-kärnan, inte 4C och inte
milestone 3-struktur. `--dry-run` skriver ingen fil. `--official` skapar
bara `forecast_snapshots/official_forecast_2026.json` och vägrar skriva
över den. Timestamp-snapshots är utkast tills `git_commit` är den commit
som innehåller kontrakt, kod och input-lås. `.gitignore` ignorerar
timestampade JSON-utkast men undantar den officiella filen så att den
kan committas.

## Datapolicy

- `data/raw` innehåller oförändrade originalfiler och committas inte.
- Varje källa har URL, hämtningstid, geografiversion, licensnotering och SHA-256.
- Bearbetade tabeller sparas som Parquet.
- En feature får bara ingå i en backtest om den var tillgänglig vid prognosens cutoff.

