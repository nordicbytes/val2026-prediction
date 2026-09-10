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
uv run pytest
```

## Datapolicy

- `data/raw` innehåller oförändrade originalfiler och committas inte.
- Varje källa har URL, hämtningstid, geografiversion, licensnotering och SHA-256.
- Bearbetade tabeller sparas som Parquet.
- En feature får bara ingå i en backtest om den var tillgänglig vid prognosens cutoff.

