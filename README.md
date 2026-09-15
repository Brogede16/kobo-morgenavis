# Kobo Morgenavis

En lille, GitHub-styret service der hver morgen bygger en EPUB: RSS-kilder og automatisk websøgning → OpenAI-redaktør → EPUB → Google Drive → Kobo.

Der er et enkelt, loginbeskyttet kontrolpanel, men ingen fancy frontend og intet Render Cron Job. I stedet er planlæggeren en del af den eksisterende Render-webservice. Det undgår en ekstra Cron-tjeneste, men kræver en **altid kørende Render-instans**. En Free-webservice kan sove og må derfor ikke bruges til den daglige planlægning.

## Sådan virker den

`sources.yaml` er redaktionens source of truth for kilder og versioneres i Git. Den henter bredt (op til 35 indslag pr. RSS-kilde), læser et lille sæt danske nyhedssektioner på **overskrifts- og previewniveau** og kombinerer det med én begrænset OpenAI-websøgning. For højst tre relevante links pr. sektion læses en kort offentlig beskrivelse eller indledning; det hjælper redaktøren med at forstå sagen uden at gøre det til en ny, token-tung AI-proces. Politiken, Information og Kulturmonitor er kun redaktionel radar: appen kopierer aldrig deres tekst eller leverer en betalingsmuret artikel, men finder i stedet tilgængelig original eller uafhængig dækning af sagen. Derefter triageres der lokalt ned til en AI-shortlist, og OpenAI vælger den færdige blanding: omkring 20-22 historier med både korte, vigtige opdateringer og 6-8 longreads/analyser. `gpt-5-mini` bruges til den billige redigering, mens den særskilte, lave-pris web-søgemodel kun bruges til ét begrænset søgekald pr. udgave. [editorial_profile.yaml](editorial_profile.yaml) er den omfattende redaktionelle profil, herunder dine læse/ikke-læse-eksempler. Se også [editorial_feedback.md](editorial_feedback.md) for processen.

Kildediversitet håndhæves også i kode: standarden er højst tre artikler pr. outlet (`edition.max_articles_per_source`), og enkelte kilder er sat lavere under `source_limits` — MacRumors højst én. Loftet er et loft, ikke en kvote: en kerne­kilde må gerne gå igen, når artiklen er det bedste bud.

## Kør nu

Avisen sendes af sig selv hver morgen. Knappen er til test.

Forsiden er et bevidst lille kontrolpanel: Åbn Render-adressen, log ind med `ADMIN_USERNAME` og `ADMIN_PASSWORD`, og tryk **Lav og send avis nu**. Kørslen starter i en baggrundstråd og siden svarer med det samme, så webservicen bliver ved med at besvare Renders healthcheck imens, og et tryk på F5 ikke kan starte en ny, betalt kørsel. Forsiden viser undervejs at kørslen er i gang, og bagefter hvor mange historier der blev sendt, eller hvad der gik galt.

Der er også en automatvenlig endpoint: `POST /run-now` med `Authorization: Bearer <RUN_NOW_TOKEN>`. Den starter kørslen og svarer `202` med det samme, også hvis en ældre klient sender `?wait=true`. Websøgning er standard; `?web_search=false` er kun til fejlsøgning. Der kan kun køre én udgave ad gangen pr. app-proces; et forsøg på nummer to svarer `409`.

## Magasinarkiv uden AI

Den samme service har et separat **Magasiner**-afsnit på kontrolpanelet. Hver dag kl. 02.15 henter den kun de konfigurerede arkivsider og ser, om der ligger nye, direkte offentlige PDF'er. Der foretages **ingen OpenAI-kald**; normale dage er derfor blot otte små HTML-opslag. Når en offentlig PDF er ny, hentes den én gang og gemmes i Drive. For hvert titelarkiv beholdes de seks nyeste offentligt tilgængelige filer; kun filer, som Mads Morgen selv har oprettet, kan ryddes op.

Det er bevidst ikke en omgåelse af betalingsmure eller digitale læsere. Magasinet MUSEUM og PROSAbladet har direkte offentlige PDF-filer i deres arkiver. Periskop er et åbent artikelarkiv, og Ud & Se, J26, KALTBLUT, On-Curating og e-flux kan have web- eller læserbaserede udgaver; når en kilde ikke tilbyder en direkte offentlig PDF, viser henteren blot dette og downloader intet. Du kan når som helst trykke **Scan og hent magasiner nu** for at få de nuværende offentlige filer uden at vente på natkørslen.

Sæt eventuelt `GOOGLE_MAGAZINES_FOLDER_ID` til en særskilt Drive-mappe, hvis du vil holde PDF'erne adskilt fra Kobo-aviserne. Uden variablen bruger henteren den eksisterende `GOOGLE_DRIVE_FOLDER_ID` (`Rakuten Kobo`). Alle magasinkilder og tidsplanen ligger i `sources.yaml` under `magazines`.

Kobo Libra Colour understøtter Google Drive direkte. Forbind Kobo-kontoen med Google Drive én gang på læseren, og brug den automatisk oprettede **`Rakuten Kobo`**-mappe som upload-mappe. Når Kobo synkroniserer over Wi‑Fi, henter den nye DRM-frie EPUB’er; de kan også ses under **More → My Google Drive**. Det er en officiel Kobo-funktion, ikke en uofficiel Kobo-API-integration.

## Lokal test

Udvælgelsen før AI prioriterer nu interesse, tilgængelig indledning og aktualitet lokalt.
De dansk/engelske signaler kan redigeres i `sources.yaml` under `curation.interest_signals`.
De er bløde prioriteringer, ikke fravalg: omkring en femtedel af shortlisten reserveres
til kildebalancerede opdagelser, og webfund beholder reserverede pladser. AI-redaktøren
foretager fortsat det endelige valg. Ukendt dato giver ikke en aktualitetsbonus.

AI'en mærker valg og reserver med emne. Hvis en artikel falder ud, forsøges først en
godkendt reserve med samme emne og format, derefter samme emne og til sidst øvrige
godkendte reserver. Kildeloft, dubletkontrol og læsbarhedskrav gælder stadig. Panelet
viser, hvis den færdige blanding har færre historier i et emne eller færre longreads
end redaktøren planlagde. Der tilføjes ingen ekstra AI-kald og intet ukureret fyldstof.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python -c "from morning_news import run_edition; print(run_edition())"
flask --app app run
```

Uden `OPENAI_API_KEY` afbrydes kørslen, så der aldrig sendes en uredigeret avis. Uden Drive-variabler gemmes EPUB’en kun i `output/`.

## Konfiguration

Redigér `sources.yaml` og commit filen. `schedule` følger cron-formatet `minut time dag måned ugedag`, og anvender tidszonen i `edition.timezone` (som standard `Europe/Copenhagen`). Standardkørslen begynder kl. 05.30, så EPUB-filen normalt ligger i Drive i god tid før kl. 06.30. Kobo skal være på Wi-Fi og synkronisere; webappen kan ikke fjernstarte selve Kobo-enhedens synkronisering. Websøgning er slået til for både planlagte og manuelle kørsler; kernespor suppleres af dagligt roterende specialspor.

### Token- og forbrugsramme

Tallene står i `sources.yaml` og er dem, der styrer forbruget. Udgaven samler op til 360 kandidater og triagerer dem gratis lokalt før AI-kaldet. 55 pladser er reserveret til web- og nyhedsradar-resultater, så RSS-mængden ikke skubber dem ud. Websøgeren får kun 200 tegn fra hvert radar-preview; de længere previews er forbeholdt den billigere redaktør. Op til 65 stærke kandidater prøvelæses lokalt før udvælgelsen; AI-redaktøren ser kun dem, hvor appen allerede har udtrukket den fulde artikeltekst, og teksten genbruges direkte i EPUB'en. Betalingsmursoverskrifter bruges samtidig som spor i den eksisterende websøgning efter åben dækning. Redaktøren ser op til 75 kildebalancerede resuméer á 400 tegn; det passer reelt under promptens hårde loft på 90.000 tegn. Der er normalt to OpenAI-kald pr. udgave: én websøgning med højst 2.200 outputtokens og selve udvælgelsen med højst 10.000. Det sidste er et sikkerhedsloft, ikke et forbrugsmål; svaret rummer højst 22 valg og ti på forhånd læsbare reserver. Midlertidige fejl får ét synligt genforsøg, mens et svar, der allerede er afkortet ved tokenloftet, stopper med en præcis fejl i stedet for at blive kørt identisk igen. Den faste redaktionelle profil ligger først i prompten, så udbyderens automatiske promptcache kan genbruges. Artiklerne omskrives ikke af AI. Den komplette redaktionelle historik bliver i Git, mens kun otte relevante positive og negative eksempler samt klikfeedback fra de seneste 120 dage indgår i en kørsel. Sæt et spend alert på OpenAI-kontoen som ekstra sikkerhedsnet.

En kørsel foregår i en separat proces med et samlet tidsloft på 30 minutter. Ved timeout dræbes og afsluttes processen før låsen frigives; fejlen vises i kontrolpanelet. En hårdt stoppet proces kan ikke selv sende en fejl-EPUB. På Linux afsluttes barnet også hvis dets overordnede proces dør. Webcachen er begrænset til 25 MB. Efter en vellykket Drive-upload fjernes lokale udgaver, der er ældre end to dage; fejl i denne oprydning eller den valgfrie feedbacknote ændrer ikke leveringen til en fejl. Fejl i websøgningen degraderer til de øvrige kilder.

Paywalls som Politiken, Information og Kulturmonitor bruges som **radar**, ikke som læseemner: Hvis en paywalled overskrift er vigtig, søger avisen efter en tilgængelig primær eller uafhængig kilde til samme historie. På samme måde skal DFI behandles som et emne, men institutionens egne pressemeddelelser og eventopslag fravælges til fordel for ekstern dækning, data eller analyse.

Kilderne har tre tydelige roller. **Læsekilder** kan levere den fulde tekst efter en lokal læsbarhedstest. **Radarkilder** bidrager kun med sager og vinkler, som websøgningen forsøger at finde åben, selvstændig dækning af. **Alternativ dækning** er en emneopdelt liste over foretrukne, læsbare domæner; den er et søgehint og ikke en kvote. Quanta Magazine, Videnskab.dk, Hyperallergic, MuseumNext og Eurogamer er specialist-læsekilder med højst én historie hver per udgave. TV 2 Kosmopol, Magasinet KBH, Arts Professional, The Verge og VGC er radar. Det udvider stoffet om videnskab, kulturdrift, København og store spilnyheder uden at lade et nichemedie overtage avisen eller tilføje flere AI-kald.

### Feedback fra den lille hjemmeside

Efter en udgave er kørt, viser forsiden dens valgte artikler med **Mere af den slags** og **Mindre af den slags**. Du kan valgfrit markere, om det drejer sig om emne, vinkel, dybde, om det er uinteressant, eller om det er godt men for nørdet. Feedback er lavet til at blive givet lejlighedsvist, ikke hver dag. Når GitHub-feedback er slået til, gemmer hvert klik artikelens titel, kilde, URL, korte resumé og dit valg i `feedback/events.jsonl` på den separate GitHub-branch `feedback-data`; dagens artikelkort arkiveres som `feedback/editions/YYYY-MM-DD.json` samme sted. Der bevares altid højst de seneste ti dagsudgaver; den ældste slettes ved næste kørsel. Den branche deployes ikke af Render, som fortsat følger `main`.

Sæt disse Render-secrets for at aktivere det: `GITHUB_REPOSITORY=Brogede16/kobo-morgenavis` og `GITHUB_FEEDBACK_TOKEN`. Tokenet skal være en GitHub fine-grained personal access token begrænset til dette ene repo med **Contents: Read and write**. Appen opretter selv `feedback-data` ved første synkronisering. Klikfeedback indgår som kompakte signaler i den næste OpenAI-udvælgelse; den redigerede, varige profil kan derefter opdateres og committes til `main`.

### Gennemsigtighed uden ekstra AI-forbrug

Forsiden viser for hver artikel den korte redaktionelle begrundelse, som allerede blev lavet ved udvælgelsen. Den viser også en ugentlig, regelbaseret note om de seneste kliksignaler og en sammenfoldet kildestatus for den pågældende udgave. Det kræver ingen ekstra OpenAI-kald: noten tæller kun den feedback, du selv har givet, og kildestatus kommer fra den allerede gennemførte indsamling. EPUB’ens overblik opdeles i **Danmark og kultur**, **Teknologi og verden** og **Fordybelse**, så den er hurtigere at skimme på Kobo.

### Langt, kort og grafik

Den færdige EPUB er en rigtig læseavis: forside, prioriteret indholdsfortegnelse, titel, kilde, fuld læsbar artikeltekst og link til originalen. Øverst står **Hvis du kun læser fem**, rangeret af redaktøren; resten følger i emneafsnit. Redaktøren tvinges til en blanding af korte nyheder og 6-8 longreads/analyser. Når `images.enabled` er aktivt, hentes hero-billeder fra sidernes Open Graph-metadata og pakker højst fire ind i en udgave, placeret under overskrift og kilde. Kun JPEG/PNG på højst 2,5 MB accepteres, så filen virker offline på Kobo Colour uden at blive unødigt stor. Hvis et billede ikke kan hentes, fortsætter artiklen pænt uden.

Hver udgave får en genereret forside med dato, antal historier, læsetid og dagens overskrifter grupperet i de tre afsnit. Den tegnes lokalt med Pillow, så den koster hverken API-kald eller ventetid. Er der et brugbart artikelbillede, sættes det ind på forsiden.

Efter upload beholder **Google Drive-mappen `Rakuten Kobo` de tre nyeste `mads-morgen-`-EPUB’er**. En manuel kørsel samme dag opdaterer dagens fil frem for at skabe en dublet. Ældre genererede aviser og gamle nummererede dubletter flyttes til Google Drives papirkurv ved næste succesfulde upload; andre filer i mappen berøres ikke. GitHub-historikken bevares separat, så gentagelser stadig kan undgås.

## Secrets og miljøvariabler

Sæt disse som Render Environment Variables — aldrig i Git:

| Variabel | Krævet | Formål |
| --- | --- | --- |
| `OPENAI_API_KEY` | Ja for AI-udvælgelse | OpenAI API-nøgle |
| `OPENAI_MODEL` | Nej | Standard er `gpt-5-mini` |
| `GITHUB_REPOSITORY` | Nej | Repo til GitHub-baseret feedback, fx `Brogede16/kobo-morgenavis` |
| `GITHUB_FEEDBACK_BRANCH` | Nej | Standard er `feedback-data`; klik her deployer ikke appen |
| `GITHUB_FEEDBACK_TOKEN` | Nej | Fine-grained token med Contents read/write til det ene repo |
| `GOOGLE_DRIVE_FOLDER_ID` | Ja for upload | ID fra Drive-mappens URL |
| `GOOGLE_MAGAZINES_FOLDER_ID` | Nej | Valgfri særskilt Drive-mappe til de offentlige magasin-PDF'er; ellers bruges Kobo-mappen |
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
3. Udfyld OpenAI-, Drive-, GitHub- og login-secrets fra tabellen ovenfor. `RUN_NOW_TOKEN` og en første admin-adgangskode oprettes automatisk af Blueprintet.
4. Deploy. Åbn `/healthz`; den viser næste planlagte kørsel og resultatet af den seneste. Åbn `/` og log ind for at køre en prøveudgave.
5. Alternativt: test med en POST til `/run-now` og headeren `Authorization: Bearer <RUN_NOW_TOKEN>`.

Render deployer automatisk ved push til `main`. Loggene i Render viser antal fundne historier, fejl pr. feed og resultatet af uploaden.

### Når noget går galt

Kørslen er bygget til at være selvkørende, så fejl skal være synlige uden at du logger ind nogen steder:

- Fejler en planlagt udgave, uploades en EPUB på én side med overskriften **Ingen avis i dag** og årsagen. Den hedder `mads-morgen-<dato>-status.epub`. Findes en rigtig avis fra samme dag, prioriteres den over fejlmeddelelsen ved oprydning.
- OpenAI-kaldet til udvælgelsen prøver to gange. En fejlformateret besvarelse gentages hurtigt, mens en 429 eller timeout får 20 sekunders pause.
- Prøvelæsning må højst bruge 60 procent af udgavens netværksbudget. Hvis grænsen nås, reserveres resten til fulde artikler og billeder, og årsagen vises særskilt i rapporten.
- Bliver servicen genstartet eller deployet omkring det planlagte tidspunkt, køres udgaven alligevel: planlæggeren har en times `misfire_grace_time`, og halvandet minut efter opstart tjekker servicen i GitHub-arkivet om dagens udgave mangler. Den henter kun op inden for fire timer efter det planlagte tidspunkt (`CATCH_UP_WINDOW_HOURS`), så et deploy om eftermiddagen ikke starter en ny betalt kørsel. Det kræver, at GitHub-feedback er sat op, for arkivet er det eneste sted der overlever en genstart.
- `/healthz` og forsiden viser tidspunkt, antal historier og eventuel fejl fra seneste kørsel.

### Hvad avisen husker fra i går

Før udvælgelsen hentes de seneste tre udgaver fra `feedback/editions/` og alt, der allerede har været bragt, sorteres fra: samme URL, samme `story_id` og næsten samme overskrift. Det koster ét GitHub-opslag og ingen AI-kald, og det er det, der forhindrer at gårsdagens sag kommer igen fra et andet medie. Antallet styres med `edition.history_editions`.

Klikfeedback fra de seneste 120 dage kan indgå i udvælgelsen, fordi feedback gives lejlighedsvist. Den varige redaktionelle profil i Git er fortsat den egentlige source of truth.

## Begrænsninger i v1

- RSS-feeds er stadig hovedkilder. Et lille, konfigureret sæt nyhedssektioner kan læses på overskriftsniveau som redaktionel radar; layout- eller adgangsændringer hos de enkelte sites kan gøre et signal midlertidigt utilgængeligt.
- Artikelsider hentes kun som almindelige web-sider. Blokerede, betalingsmurede eller tydeligt ufuldstændige artikler kommer ikke med i EPUB’en.
- Drive-import til Kobo er et Kobo-trin, ikke en automatisk push-kanal.
