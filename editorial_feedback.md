# Redaktionel feedback

Dette er den versionsstyrede huskeliste for ændringer i morgenavisen.

## Sådan giver du feedback i Codex

Skriv naturligt, for eksempel:

- “Mere dansk erhverv og mindre sport.”
- “Fjern The Guardian og tilføj BBC Technology.”
- “Højst fem historier og ingen AI-opsummering.”
- “Brug web-søgning på hverdage, men ikke i weekenden.”

Codex omsætter feedbacken til en konkret ændring i `sources.yaml` (kilder, emner, antal, søgeord eller budget) og tilføjer en kort post nedenfor. Ændringen testes, committes og pushes, når GitHub-remote er sat op. Der må aldrig gemmes API-nøgler eller adgangskoder i denne fil.

Når du sender links til artikler, du ville eller ikke ville læse, gemmer Codex også en kort begrundelse i `editorial_profile.yaml`. Det er en del af den samlede redaktionelle profil, sammen med prioriteringer, mix, undtagelser og stil. Det betyder, at udvælgelsen lærer artikeltypen og vinklen — ikke bare domænet. Fx kan produktanalyser fra MacRumors vælges, mens podcast-lanceringer fra samme domæne fravælges.

## Historik

- 2026-09-07: V1 oprettet med danske/verdens-, teknologi-, videnskabs- og kultur-emner.
- 2026-09-07: Første kalibrering: prioriter substans, analyse og konsekvenser. Fravælg isolerede tekniske specifikationer og sensationspræget AI-risiko uden brugbar indsigt. Udvidet med dansk/international politik, samfund og økonomi.
- 2026-09-07: Tilføjet museumsudvikling, publikumsdata, kulturinstitutioners partnerskaber, biograføkonomi, praktisk AI, astronomi, væsentlige wearable-nyheder, selektive topnyheder om spil og seriøs foto-læring. Kultur-/filmgossip vælges kun, når den belyser magt, penge, konflikt, ejerskab eller kreativ retning.
