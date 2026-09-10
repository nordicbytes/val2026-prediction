# Valmyndigheten source inventory

Live-verifierad 2026-09-10. Alla filer är fria att återanvända med
Valmyndigheten angiven som källa. Ingen namngiven Creative Commons-licens
anges.

| Val | Distriktsresultat | Geometri | Valdistriktsdemografi | Mapping |
|---|---|---|---|---|
| 2006 | ZIP/XLS och XML | SHP, RT90 | saknas | saknas |
| 2010 | XLS/SKV och full XML | SHP, EPSG:3006 | ålder/kön | saknas |
| 2014 | XLS/SKV och full XML | SHP, EPSG:3006 | ålder/kön | ingen 2010→2014 |
| 2018 | XLSX | SHP, EPSG:3006 | ålder/kön | 2014→2018 med procent |
| 2022 | XLSX, long-format | JSON per län | ålder/kön | 2018→2022 kvalitativ |
| 2026 | ännu ej publicerat | GeoJSON, EPSG:3006 | ålder/kön | 2022→2026 kvalitativ |

## Resultatfiler

- 2006: `https://historik.val.se/val/val2006/slutlig_ovrigt/statistik/riksdag/riksdagen_i_valdistrikt_excel.zip`
- 2010: `https://historik.val.se/val/val2010/slutresultat/slutresultat.zip`
- 2014: `https://historik.val.se/val/val2014/slutresultat/slutresultat.zip`
- 2018: `https://historik.val.se/val/val2018/statistik/2018_R_per_valdistrikt.xlsx`
- 2022: registrerad och checksummad i `config/sources.yaml`
- 2026: `https://resultat.val.se/resultatfiler/val2026/index.md5`

2026-indexet finns men är tomt före valet. Pipeline får inte tolka ett tomt
index som ett lyckat resultatingest.

## Geometrier

- 2006: `https://historik.val.se/val/val2006/slutlig_ovrigt/statistik/kartor/riksdagen_i_valdistrikt.zip`
- 2010: `https://historik.val.se/val/val2010/statistik/gis/alla_valdistrikt.zip`
- 2014: `https://historik.val.se/val/val2014/statistik/gis/valgeografi_valdistrikt.zip`
- 2018 och 2026: registrerade och hämtade via source manifestet.
- 2022: 21 länsvisa ZIP-filer från rådatasidan; ingen officiell rikstäckande
  fil hittades.

2022-filerna saknar CRS-nyckel i innehållet. EPSG:3006 kommer från
Valmyndighetens dokumentation och måste sparas som ett explicit
normaliseringsantagande.

## Officiella mappings

- 2014→2018:
  `https://historik.val.se/val/val2018/statistik/mappning_2014_2018.zip`
- 2018→2022: registrerad, hämtad och parsad.
- 2022→2026: registrerad och hämtad.

Ingen officiell mapping hittades för 2006→2010 eller 2010→2014. Dessa luckor
får inte fyllas genom kod- eller namnjoin. GIS-overlay krävs om övergångarna
senare ska användas.

## URL-risk

Historik- och `data.val.se/filer/...`-URL:er är semantiska. Moderna
`/download/18.../{timestamp}/...`-URL:er är SiteVision-ID:n och kan ändras när
filer publiceras om. Source manifestets checksumma upptäcker innehållsbyte,
men katalogsidorna är den kanoniska upptäcktskällan.

