# Kobo Morgenavis

En lille, GitHub-styret service der hver morgen bygger en EPUB: RSS-kilder → AI-redaktør → EPUB → Google Drive → Kobo.

Der er ingen frontend og ingen Render Cron Job. I stedet er planlæggeren en del af den eksisterende Render-webservice. Det er bevidst for at undgå ekstra Cron-omkostning, men den kræver en **altid kørende Render-instans**. En Free-webservice kan sove og må derfor ikke bruges til den daglige planlægning. `render.yaml` bruger `starter`; hvis din eksisterende service allerede holdes vågen, kan den samme kode i stedet indgå dér.

## Sådan virker den

`sources.yaml` er redaktionens source of truth for kilder og versioneres i Git. Den henter bredt (op til 25 indslag pr. kilde), kombinerer det med tre faste Google-søgninger og triagerer lokalt ned til en AI-shortlist. Derefter vælger Gemini den færdige blanding: 6-7 korte, vigtige opdateringer og 2-3 longreads/analyser. [editorial_profile.yaml](editorial_profile.yaml) er den omfattende redaktionelle profil, herunder dine læse/ikke-læse-eksempler. Se også [editorial_feedback.md](editorial_feedback.md) for processen.

Kildediversitet håndhæves også i kode: standarden er højst to artikler pr. outlet, og MacRumors højst én. Rest of World og 404 Media indgår som web-radarer, så teknologidækningen ikke alene følger produktnyheder eller de største teknologimedier.

## Kør nu

Forsiden er et bevidst lille kontrolpanel: Åbn Render-adressen, log ind med `ADMIN_USERNAME` og `ADMIN_PASSWORD`, og tryk **Lav og send avis nu**. Den kører hele kæden med det samme og uploader EPUB’en til Google Drive. Du kan valgfrit markere “Søg også på nettet denne ene gang”. Det kræver ingen ny Render-tjeneste.

Der er også en automatvenlig endpoint: `POST /run-now` med `Authorization: Bearer <RUN_NOW_TOKEN>`. Tilføj `?web_search=true` kun når den ekstra web-søgning ønskes.

Kobo Libra Colour understøtter Google Drive direkte. Forbind Kobo-kontoen med Google Drive én gang på læseren, og brug den automatisk oprettede **`Rakuten Kobo`**-mappe som upload-mappe. Når Kobo synkroniserer over Wi‑Fi, henter den nye DRM-frie EPUB’er; de kan også ses under **More → My Google Drive**. Det er en officiel Kobo-funktion, ikke en uofficiel Kobo-API-integration.

## Lokal test

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python -c "from morning_news import run_edition; print(run_edition())"
flask --app app run
```

Uden `GEMINI_API_KEY` vælger den de første fundne historier, så resten af kæden stadig kan testes. Uden Drive-variabler gemmes EPUB’en kun i `output/`.

## Konfiguration

Redigér `sources.yaml` og commit filen. `schedule` følger cron-formatet `minut time dag måned ugedag`, og anvender tidszonen i `edition.timezone` (som standard `Europe/Copenhagen`). `web_search` er slået fra for den planlagte kørsel som standard, men kan vælges på “kør nu”-siden eller bevidst aktiveres i konfigurationen.

### Token- og forbrugsramme

Den normale udgave samler op til 180 RSS-kandidater, men bruger gratis lokal triage før AI-kaldet. Redaktøren ser højst 80 korte resuméer á 260 tegn og må bruge 400 outputtokens. Gemini Google Search-grounding er slået til automatisk med fem redaktionelt afgrænsede søgninger og 700 outputtokens. Artiklerne omskrives ikke af AI. Det giver bred dækning uden at sende fulde artikler eller et ubegrænset antal kandidater til modellen. Den komplette redaktionelle historik bliver i Git, mens de seneste 12 positive og 12 negative feedback-eksempler indgår i en kørsel. Sæt et projektbudget/spend alert på Google AI-kontoen som ekstra sikkerhedsnet.

Paywalls som Information og Kulturmonitor bruges som **radar**, ikke som læseemner: Hvis en paywalled overskrift er vigtig, søger avisen efter en tilgængelig primær eller uafhængig kilde til samme historie. På samme måde skal DFI behandles som et emne, men institutionens egne pressemeddelelser og eventopslag fravælges til fordel for ekstern dækning, data eller analyse.

### Langt, kort og grafik

Den færdige EPUB er en rigtig læseavis: forside, indholdsfortegnelse, titel, kilde, fuld læsbar artikeltekst og link til originalen. Redaktøren tvinges til en blanding af korte nyheder og 2-3 longreads/analyser. Når `images.enabled` er aktivt, hentes højst ét hero-billede pr. artikel fra sidens Open Graph-metadata og pakkes *ind i* EPUB’en. Kun JPEG/PNG på højst 2,5 MB accepteres, så filen virker offline på Kobo Colour uden at blive unødigt stor. Hvis et billede ikke kan hentes, fortsætter artiklen pænt uden.

## Secrets og miljøvariabler

Sæt disse som Render Environment Variables — aldrig i Git:

| Variabel | Krævet | Formål |
| --- | --- | --- |
| `GEMINI_API_KEY` | Ja for AI-udvælgelse | API-nøgle fra Google AI Studio / Gemini API |
| `GEMINI_MODEL` | Nej | Standard er `gemini-2.5-flash` |
| `GOOGLE_DRIVE_FOLDER_ID` | Ja for upload | ID fra Drive-mappens URL |
| `GOOGLE_OAUTH_CLIENT_ID` | Ja for normal Drive-upload | OAuth-klient-id fra dit Google Cloud-projekt |
| `GOOGLE_OAUTH_CLIENT_SECRET` | Ja for normal Drive-upload | OAuth-klienthemmelighed |
| `GOOGLE_OAUTH_REFRESH_TOKEN` | Ja for normal Drive-upload | Langlivet brugeradgang til Kobo-mappen |
| `GOOGLE_SERVICE_ACCOUNT_JSON_B64` | Alternativ | Base64-kodet service-account JSON |
| `RUN_NOW_TOKEN` | Anbefalet | Beskytter `POST /run-now` |
| `ADMIN_USERNAME` | Nej | Brugernavn til den lille kør-nu-side; standard `mads` |
| `ADMIN_PASSWORD` | Ja for kør-nu-side | Stærkt, unikt login til forsiden |

En almindelig **Google API key kan ikke skrive til din private Google Drive**; den identificerer et projekt, men giver ikke brugeradgang. Brug i stedet en OAuth-klient med `drive.file`-adgang som standard. Det lader Render skrive kun til den udpegede **`Rakuten Kobo`**-mappe på dine vegne. Gem client-id, client-secret og refresh token som Render-secrets. Service account er stadig en mulig reserve, men Google advarer om, at den ikke ejer filer i din personlige Drive på samme måde.

Hvis service account bruges: del den Kobo-oprettede **`Rakuten Kobo`**-mappe med service-accountens e-mailadresse som **Editor**, og kod JSON-nøglen til én base64-linje:

```bash
base64 -i service-account.json | tr -d '\n'
```

## Render-deploy

1. Opret et GitHub-repo og push denne mappe til `main`.
2. I Render: vælg **New → Blueprint**, forbind repoet, og lad Render læse `render.yaml`.
3. Udfyld de tre secrets i tabellen ovenfor. `RUN_NOW_TOKEN` oprettes automatisk af Blueprintet.
4. Deploy. Åbn `/healthz`; den viser næste planlagte kørsel. Åbn `/` og log ind for at køre en prøveudgave.
5. Alternativt: test med en POST til `/run-now` og headeren `Authorization: Bearer <RUN_NOW_TOKEN>`.

Render deployer automatisk ved push til `main`. Loggene i Render viser antal fundne historier, fejl pr. feed og resultatet af uploaden.

## Begrænsninger i v1

- Kun RSS-feeds i første version; enkelte websites kan senere tilføjes som særskilte adapters.
- Artikelsider hentes kun som almindelige web-sider og kan falde tilbage til RSS-resumé, hvis de er blokerede.
- Drive-import til Kobo er et Kobo-trin, ikke en automatisk push-kanal.
