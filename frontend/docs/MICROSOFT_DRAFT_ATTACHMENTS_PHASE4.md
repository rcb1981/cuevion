# Microsoft Drafts attachments — fase 4

Datum: 24 oktober 2026. Branch: `codex/microsoft-drafts-recovery`.
De bestaande, ongecommitte fase 3-adapters zijn behouden en uitgebreid.

## Interface

Het Microsoft-conceptvenster bevat een bestandskiezer met meervoudige selectie,
drag-and-drop, bestandskaartjes en verwijdering uit de lokale selectie. Kaartjes
gebruiken de bestaande Microsoft-bijlagepresentatie die de Gmail/IMAP-vormgeving
volgt: naam, type, icoon, leesbare grootte en een afzonderlijke verwijderknop.
Selecties blijven beperkt tot 10 bestanden, 2 MiB per bestand en 8 MiB totaal.
De interface toont voorbereiden, uploaden, verwijderen, controleren en de door
de ledger bevestigde aantallen, inclusief gedeeltelijke resultaten.

Bestaande gewone bijlagen krijgen uitsluitend een verwijderactie als de
serverinventaris `removable` bevestigt. Inline/CID-bijlagen krijgen geen
verwijderactie; overige niet-verwijderbare bijlagentypen blijven beschermd.

## Uitvoering en herstel

Toevoegen en verwijderen gebruiken alleen de fase 3-Microsoft-adapter. Er is een
synchrone blokkade vóór bestandsvoorbereiding en verzending. De teksteditor en
bijlageneditor blokkeren elkaars acties; onopgeslagen tekstwijzigingen moeten
eerst worden opgeslagen. Er zijn geen automatische mutation retries.

De nieuwe eigenaargebonden `draft_status`-leesactie haalt de duurzame receipts
voor het exacte concept op. Herladen en terugkeren na een berichtwissel vereisen
geen browseropslag of bewaard request-ID. Bestanden en base64-inhoud bestaan
uitsluitend in het geheugen van de selectie en de lopende aanvraag.

Onzekere receipts, verloren antwoorden, ontbrekende status en verlopen
verbindingen blokkeren nieuwe acties. De knop voor statuscontrole doet alleen
leesacties. Een gedeeltelijke selectie wordt nooit automatisch voortgezet.
Een onzeker ledgerrecord wordt niet op basis van een browserwaarneming gewist.
Ook de backend blokkeert tekstwijzigingen bij een onzeker bijlagenrequest.

Bij het starten van een mutatie wordt de oude conceptinhoud uit de achterliggende
weergave verwijderd. Bevestigde wijzigingen sluiten het editorvenster en laden
het exacte concept en de lijst opnieuw. Late antwoorden van een verlaten
concept kunnen geen ander bericht bijwerken.

## Tekst, HTML en behoud

Achter de nog uitgeschakelde bijlagenvlag kunnen ondersteunde tekst- en
HTML-metadataconcepten gewone bestaande bijlagen behouden tijdens opslaan.
De bestaande conditionele PATCH bevat geen `attachments`-veld en inventaris-
identiteiten en metadatahashes worden voor en na opslaan vergeleken. Afwijkingen
worden niet als succes bevestigd. De HTML-metadata-PATCH bevat geen berichttekst;
de oorspronkelijke HTML-hash wordt gecontroleerd. De bestaande HTML-, Bcc- en
CID-beperkingen blijven gelden. De bijlagenadapter beschermt inline/CID-inhoud
onafhankelijk van deze teksteditors.

## Verificatie

Alle API-tests gebruiken fake Graph en geïsoleerde opslag. Testrunners blokkeren
extern netwerkverkeer en verwijderen geërfde credentials.

- Microsoft-backend: **635 tests geslaagd**, nul externe I/O-pogingen.
- Nieuwe React-interacties: **19/19 geslaagd**.
- Microsoft-clientrunner: **44/46 suites geslaagd**; twee bestaande fouten.
- Brede offline clientrunner: **131/136 suites geslaagd**; vijf bestaande fouten.
- TypeScript: `tsc -b frontend/tsconfig.json --pretty false` geslaagd.
- Frontend: `npm run build --prefix frontend` geslaagd.
- Gmail/IMAP-bronbeveiliging: **645 beschermde bestanden ongewijzigd**.
- `git diff --check`: geslaagd.

De vijf bestaande clientfouten zijn ook met de oorspronkelijke branchbron
reproduceerbaar: `App.collaborationGuestRoute.test.ts`,
`MicrosoftFilteredSenderAction.test.ts`, `MicrosoftMailboxFolderRail.test.ts`,
`inboxConnectionApi.gmailOwnership.test.ts` en `mailboxDisplayName.test.ts`.
De twee Microsoft-fouten betreffen verouderde filtering- en breakpointverwachtingen.
Deze vallen buiten fase 4 en zijn niet aangepast.

Logbestanden staan lokaal onder `/tmp/cuevion-ms-phase4-*`. De volledige
clientrapporten bevatten per suite de opdracht, exitcode en netwerkcontrole.
De baselinevergelijking staat in `/tmp/cuevion-ms-phase4-baseline/results.json`.
De build meldt de bestaande Browserslist-leeftijd en grote bundelchunks.

## Beperkingen en acceptatie

Technisch gereed voor gerichte offline acceptatietests met fake Graph. De brede
clienttestsuite is door de genoemde bestaande fouten niet volledig groen.
Live Graph-acceptatie en productievrijgave zijn niet uitgevoerd of vastgesteld.
Alle nieuwe releasevlaggen blijven uit. Er zijn geen echte mailboxmutaties,
Compose/Reply/Forward-wijzigingen, Gmail/IMAP-wijzigingen, commits, pushes of
deployments uitgevoerd; Production is ongewijzigd.

Een werkelijk onzeker resultaat blijft geblokkeerd tot een afzonderlijk,
betrouwbaar verificatieproces de ledger kan afhandelen; deze interface biedt
geen geforceerde herhaling of ontgrendeling. De bestaande begrensde ledger
blijft zonder automatische verwijdering of eviction werken. Niet-geüploade
bestandsselecties worden bewust niet na een browserherlaad hersteld.
