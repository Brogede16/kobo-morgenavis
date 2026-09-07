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
- 2026-09-07: Morgenavisen skal gøre læseren klædt på til dagens nyheder og samtaler. Skærpet dansk politik og kulturpolitik samt store kulturudgivelser og fysiske begivenheder med reel dansk relevans. DR Kultur bruges som et aktivt spor via den automatiske websøgning.
- 2026-09-07: Kulturprioriteringen er et supplement, ikke en erstatning for AI, Apple, teknologi, videnskab, dansk/international politik og samfund. Spil: vælg kun store nyheder om Warcraft, Age of Empires, survival, 7 Days to Die og GTA. Planet Zoo er en særlig dyb interesse; efterfølger, større udvidelse eller markant kursændring fortjener mere kontekst.
- 2026-09-07: Udvidet med konkret publikumsudvikling, kulturinstitutioners nye formater/indtægter, partnerskaber, publikumsdata og praktisk kulturteknologi. Skærpet AI mod agenter, vibe coding og særlige fremadskuende idéer; videnskab mod rum, kosmologi, store fund, interaktive data, infrastruktur og skjulte systemer. Tilføjet høj-tærskel spilregel, kurateret foto-læring, personlig teknologi/nørdeprojekter, store band-nyheder og subkulturer.
- 2026-09-07: Tilføjet København under forandring, kulturinstitutioners drift/hospitality/retail/kuratering/prissætning og eventproduktion, unge som medskabere, kulturtrends og internetæstetik. Udvidet med dyrekognition, astronomiske anomalier, geoengineering, forståelig cybersikkerhed, hverdagsstandarder, betydningsfuld biografteknik, Københavns kulturhistorie, Danmark under jorden og kritisk infrastruktur samt AI-byggede spilprototyper.
- 2026-09-07: Musikspor justeret: Pink Floyd er relevant; Depeche Mode er fjernet.
- 2026-09-07: Politik skal være konkret: sigt efter 1-2 danske historier om beslutninger, lovgivning, budgetter, konflikter eller konsekvenser. Politiken, Information og Kulturmonitor er redaktionel radar, men betalingsmurede artikler erstattes af tilgængelig original eller uafhængig dækning. Fravælg officielle nyhedsroundups og tætte forskningsmeddelelser som læsestof, når en klar journalistisk forklaring findes.
