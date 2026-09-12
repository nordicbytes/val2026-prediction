# Förtidsröstning som prognossignal

## Slutsats

**Ingen av de två förregistrerade signalerna klarar gaten.** Offentlig statistik
över var förtidsröster tas emot ska därför inte påverka 2026-prognosen.

Partimodellen försämrar det röstantalsviktade felet i samtliga tre hållna val
och är 10,0 procent
sämre totalt. Valdeltagandemodellen förbättrar 2014 och 2022, men blir tydligt
sämre 2018. Den lilla poolade förbättringen på
1,1 procent är inte
stabil mellan val.

## Förregistrerad fråga och design

Designen låstes i git innan någon score räknades (`7a7869b`, med en ren
YAML-nestningsfix i `6a11a56`). Testet frågar om takten i mottagna
förtidsröster innehåller geografisk information efter att föregående
kommunresultat och målvalets nationella rörelse redan räknats bort.

- Officiella mottagningsdata för 2010, 2014, 2018 och 2022.
- 290 kommuner per målval; 870 kommun-val-observationer.
- Avskärning D−2, motsvarande fredagen före ett söndagsval.
- Sex förregistrerade egenskaper: nivå och förändring i mottagna röster per
  röstberättigad, andelen mottagen senast D−7 samt antal aktiva lokaler.
- Ridge-regression med fast `alpha=10`, tränad på två val och testad på det
  tredje.
- Baslinjen för partier är föregående kommunresultat med målvalets verkliga
  nationella proportionella svängning. Det är en stark oracle-baslinje som
  isolerar frågan om lokal signal.
- Gaten kräver förbättring i varje hållet val och minst 0,5 procent poolat.

## Källdata vid D−2

| Val | Kommuner | Mottagna röster |
|---:|---:|---:|
| 2010 | 290 | 2 131 401 |
| 2014 | 290 | 2 388 188 |
| 2018 | 290 | 2 612 525 |
| 2022 | 290 | 2 820 437 |

Filerna är Valmyndighetens slutliga breda filer med en kolumn per mottagningsdag.
De gör det möjligt att återskapa antalet röster som mottogs före D−2, men inte
exakt den filversion en analytiker hade D−2: sena registreringar kan ha fyllts
bakåt. Detta gör testet något mer välvilligt mot signalen än en verklig
realtidskörning.

## Resultat: partier

MAE i procentenheter, åtta namngivna partier, viktat med giltiga röster.
Positiv förändring betyder att kandidaten förbättrar baslinjen.

| Hållet val | Baslinje | Med förtidsröster | Relativ förbättring |
|---:|---:|---:|---:|
| 2014 | 0,751 | 0,938 | -24,9 % |
| 2018 | 0,963 | 1,013 | -5,2 % |
| 2022 | 0,905 | 0,933 | -3,1 % |

Poolat: baslinje 0,874 pp,
kandidat 0,962 pp,
-10,0 %. **Gate: FAIL.**

## Resultat: valdeltagande

MAE i procentenheter, viktat med röstberättigade.

| Hållet val | Baslinje | Med förtidsröster | Relativ förbättring |
|---:|---:|---:|---:|
| 2014 | 0,653 | 0,609 | +6,7 % |
| 2018 | 0,357 | 0,510 | -42,9 % |
| 2022 | 0,901 | 0,774 | +14,1 % |

Poolat: baslinje 0,640 pp,
kandidat 0,633 pp,
+1,1 %. **Gate: FAIL.**

## Bokstavlig kontroll av idén

Som en efterhandsdiagnostik, utan möjlighet att ändra gaten, viktades föregående
valårs kommunresultat med var målvalets förtidsröster hade tagits emot. Det
jämfördes med att lämna föregående nationella resultat oförändrat.

| Målval | Föregående resultat | Förtidsvägd geografi | Relativ förbättring |
|---:|---:|---:|---:|
| 2014 | 2,240 | 2,215 | +1,1 % |
| 2018 | 2,500 | 2,522 | -0,9 % |
| 2022 | 1,436 | 1,410 | +1,8 % |

Effekten är mycket liten och byter tecken: marginellt bättre 2014 och 2022,
marginellt sämre 2018. Det finns därför inget stabilt stöd för att
röstningslokalernas omgivande historik beskriver vilka som har röstat där.

## Varför signalen sannolikt faller

Den offentliga filen mäter **mottagningsplats**, inte väljarens hemadress.
Stationer, köpcentrum, sjukhus och arbetsplatser tar emot väljare från andra
kommuner och valdistrikt. Sambandet mellan lokalens omgivning och väljarnas
politiska sammansättning blir därför både svagt och olika från val till val.

Dessutom berättar antalet mottagna kuvert inget om parti. När modellen försöker
översätta en aktivitetsnivå till partiförändring lär den sig huvudsakligen
tillfälliga samband från två val, vilka inte håller i det tredje.

## 2026

Ingen 2026-känslighetsprognos produceras:

1. både parti- och valdeltagandegaten faller, och
2. den nuvarande officiella 2026-filen hämtades efter datastoppet
   11 september 22:29 CEST och kan innehålla efterregistreringar.

En bättre fortsättning kräver anonymiserade antal per **hemvaldistrikt** från
kommunernas Valid-export, helst även historiska publiceringsvintages. Det skulle
testa faktisk lokal förtidsröstning i stället för trafiken genom en
röstningslokal.
