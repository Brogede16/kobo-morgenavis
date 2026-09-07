# Kobo Morgenavis

En lille, GitHub-styret service der hver morgen bygger en EPUB: RSS-kilder → AI-redaktør → EPUB → Google Drive → Kobo.

Der er ingen frontend og ingen Render Cron Job. I stedet er planlæggeren en del af den eksisterende Render-webservice. Det er bevidst for at undgå ekstra Cron-omkostning, men den kræver en **altid kørende Render-instans**. En Free-webservice kan sove og må derfor ikke bruges til den daglige planlægning. `render.yaml` bruger `starter`; hvis din eksisterende service allerede holdes vågen, kan den samme kode i stedet indgå dér.

## Sådan virker den

`sources.yaml` er redaktionens source of truth og versioneres i Git. Ved hver kørsel hentes aktive RSS-feeds, OpenAI udvælger de mest relevante og varierede historier, artiklerne renderes til en EPUB, og filen lægges i den valgte Google Drive-mappe.

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

Uden `OPENAI_API_KEY` vælger den de første fundne historier, så resten af kæden stadig kan testes. Uden Drive-variabler gemmes EPUB’en kun i `output/`.

## Konfiguration

Redigér `sources.yaml` og commit filen. `schedule` følger cron-formatet `minut time dag måned ugedag`, og anvender tidszonen i `edition.timezone` (som standard `Europe/Copenhagen`).

## Secrets og miljøvariabler

Sæt disse som Render Environment Variables — aldrig i Git:

| Variabel | Krævet | Formål |
| --- | --- | --- |
| `OPENAI_API_KEY` | Ja for AI-udvælgelse | Nøglen til OpenAI API |
| `OPENAI_MODEL` | Nej | Standard er `gpt-5-mini` |
| `GOOGLE_DRIVE_FOLDER_ID` | Ja for upload | ID fra Drive-mappens URL |
| `GOOGLE_SERVICE_ACCOUNT_JSON_B64` | Ja for upload | Base64-kodet Google service-account JSON |
| `RUN_NOW_TOKEN` | Anbefalet | Beskytter `POST /run-now` |

Opret en Google Cloud service account med Drive API aktiveret, del den Kobo-oprettede **`Rakuten Kobo`**-mappe med service-accountens e-mailadresse som **Editor**, og kod JSON-nøglen til én base64-linje:

```bash
base64 -i service-account.json | tr -d '\n'
```

## Render-deploy

1. Opret et GitHub-repo og push denne mappe til `main`.
2. I Render: vælg **New → Blueprint**, forbind repoet, og lad Render læse `render.yaml`.
3. Udfyld de tre secrets i tabellen ovenfor. `RUN_NOW_TOKEN` oprettes automatisk af Blueprintet.
4. Deploy. Åbn `/healthz`; den viser næste planlagte kørsel.
5. Test manuelt med en POST til `/run-now` og headeren `Authorization: Bearer <RUN_NOW_TOKEN>`.

Render deployer automatisk ved push til `main`. Loggene i Render viser antal fundne historier, fejl pr. feed og resultatet af uploaden.

## Begrænsninger i v1

- Kun RSS-feeds i første version; enkelte websites kan senere tilføjes som særskilte adapters.
- Artikelsider hentes kun som almindelige web-sider og kan falde tilbage til RSS-resumé, hvis de er blokerede.
- Drive-import til Kobo er et Kobo-trin, ikke en automatisk push-kanal.
