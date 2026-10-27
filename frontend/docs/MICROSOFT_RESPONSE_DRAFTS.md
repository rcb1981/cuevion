# Microsoft Reply, Reply All en Forward — implementatieverslag

Branch: `codex/microsoft-drafts-recovery`. Deze ronde bevat alleen lokale code en
offline verificatie. Alle bestaande releasevlaggen en de nieuwe afzonderlijke
Reply/Forward-vlag staan uit. Production, onboarding, OAuth-appregistraties,
credentials en bestaande Gmail/IMAP-functionaliteit zijn niet gewijzigd.

## Wat is geïmplementeerd

De bestaande Microsoft-werkbalk opent voor Reply, Reply All en Forward hetzelfde
`MicrosoftComposeDraftPanel` als gewone concepten. Openen doet uitsluitend
geautoriseerde reads. Alleen de expliciete knop Save draft veroorzaakt een
conceptmutatie. De oude `/send-microsoft`-route wordt hiervoor niet gebruikt.
De interface ondersteunt Inbox, Filtered, Archive en Sent als geselecteerde bron.

Het Gmail/IMAP-patroon is als uitgangspunt gelezen in `WorkspaceShell.tsx`:
Reply richt zich op de antwoordontvanger; Reply All voegt overige To/Cc-adressen
toe in Cc, dedupliceert en sluit de eigen mailbox uit. Microsoft houdt bovendien
rekening met Graph `replyTo`, met `from` als terugval. Een antwoord op een bericht
van het eigen mailboxadres richt zich op de andere oorspronkelijke To-adressen.
Bcc wordt nooit uit de bron overgenomen. Forward start met lege ontvangers.
Ontvangers en onderwerp blijven bewust bewerkbaar. Alle adressen worden opnieuw
serverzijdig gevalideerd. De eigen primaire mailbox wordt uitgesloten; volledige
zelfherkenning van aanvullende Microsoft-aliassen is niet geïmplementeerd.

Bij Save gebruikt de server Graph `createReply`, `createReplyAll` of
`createForward`. Graph maakt het oorspronkelijke antwoord-/doorstuurconcept;
voor antwoorden wordt de conversationId onafhankelijk tegen de bron gecontroleerd.
De server slaat het exacte nieuwe immutable ID duurzaam op voordat hij verdergaat.
Een afzonderlijke conditionele PATCH met If-Match voegt de gebruikersbijdrage en
gekozen headers toe. Er wordt nergens een send/reply/replyAll/forward-sendactie
aangeroepen. De directe Graph-acties zonder `create` worden niet gebruikt.

Opnieuw openen gebeurt via bestaande Drafts en Compose. HTML-concepten hebben een
extra knop Edit in Compose, onafhankelijk van de Reply/Forward-vlag. Daarmee
heropenen tekstconcepten, geregistreerde HTML-concepten en geregistreerde responses.
Responsebewerkingen behouden hun eigen afzonderlijke releasecontrole. Voor andere HTML-concepten blijft de
bestaande headereditor beschikbaar. De nieuwe editor haalt de eigen tekst terug,
laat de quote als inert tekstvoorbeeld zien en bewaart de volledige Graph-quote
onaangetast. De bestaande veilige HTML/CID-reader toont het volledige opgeslagen
bericht in Drafts. Geen algemene reader of MIME/Send-vergelijking is versoepeld.

Forward controleert gewone fileAttachment-bestanden op volledige inventaris,
metadata en bytehashes vóór en na kopiëren én vóór en na de header/body-PATCH.
Vervanging met dezelfde naam en grootte wordt dus gedetecteerd. Grenzen blijven
10 bestanden, 2 MiB per bestand en 8 MiB totaal. Item-, reference-, inline/CID- en
onbekende typen worden vóór het maken van een Forward-concept geblokkeerd.

## Beveiliging en bewijskracht

De bestaande eigenaarcontrole, token-/accountidentiteit, credential generation,
mailboxlease, CAS-conceptledger, bijlageblokkades en folder-onafhankelijke Send-index
blijven actief. Bronberichten met een Send-blokkade kunnen niet als nieuwe response
worden gebruikt. Bestaande responseconcepten met een blokkade kunnen niet worden
bijgewerkt. Onleesbare noodzakelijke opslag blokkeert. Een reconnect creëert geen
nieuwe claim voor een bestaande request-ID.

De gedeelde Compose-ledger reserveert de operatie vóór de eerste Graph-mutatie.
Na een verloren antwoord wordt dezelfde request-ID alleen teruggelezen. Een
onzekere create blokkeert ook nieuwe create-ID's; er is geen automatische retry,
cleanup, ontgrendeling of hervatting van de tweede PATCH. Een gedeeltelijk voltooid
resultaat kan daarom een quote-only concept in Drafts achterlaten. Dit wordt niet
als voltooid opgeslagen aangemerkt en wordt niet automatisch verwijderd.

Per managed responseconcept is een aparte CAS-provenance opgeslagen onder
`cuevion:microsoft:response-draft:v1:`. Die bevat scopehash, versie, modus,
conversationId, bodytype, exacte quotehash en prefix-/suffixlengtes. Geen volledige
body, ontvangeradressen, credentials of bijlagebytes worden hierin opgeslagen.
De bestaande Compose-recordschema's blijven leesbaar. Er is geen nieuwe database,
testinfrastructuur, OAuth-configuratie of onboardingroute toegevoegd.

De comment wordt als escaped tekst of gevalideerd inert HTML-fragment geplaatst; de grens van
het HTML-bodyelement wordt met een HTML-parser bepaald, inclusief aangehaalde
attributen. Bij heropenen moeten het fragment en de overige quote exact aan het
opgeslagen contract voldoen. Externe quoteaanpassing of Graph-normalisatie wordt
niet via een zichtbare-tekstvergelijking geaccepteerd.

Voor de initiële Graph-quote is Graph zelf de constructieautoriteit. Een volledige
zichtbare-tekstcontrole detecteert een ontbrekende/afgekorte bronquote, maar bewijst
geen byte-identiteit of semantische gelijkwaardigheid van de oorspronkelijke HTML.
Daarna wordt de exact waargenomen Graph-quote hashmatig behouden. De gebruiker moet
de opgeslagen inhoud kunnen beoordelen. Deze controle is geen goedgekeurd
Send-manifest en geen bewijs van verzending. HTML-normalisatie door echte Graph
is nog niet live bewezen; onbewijsbare veranderingen blijven onzeker/geblokkeerd.
Ook het externe Outlook-racevenster is niet atomisch opgeheven: wijzigingen tussen
waarnemingen worden waarneembaar gecontroleerd, niet door een Graph-transactie
voorkomen.

## Grenzen voor volledige pariteit

- Deze ronde levert de conceptflow; responses verzenden blijft geblokkeerd. Het
  bestaande strikte Send-contract accepteert geen onbewezen HTML/MIME-equivalentie.
- De eigen bijdrage ondersteunt nu basis-HTML-opmaak; de oorspronkelijke quote
  blijft beschermd. Geen willekeurige HTML-quote-editor. Outlook-wijzigingen aan de quote vereisen
  beoordeling, zonder automatische overschrijving of herstel.
- Forward ondersteunt niet alle Microsoft-bijlagentypen of grote bestanden.
- Volledige aliasherkenning en een Reply/Reply All-schakelaar midden in dezelfde
  Compose-sessie ontbreken. Beide modi zijn afzonderlijk beschikbaar.
- De grens van 32 Compose-operaties is verwijderd; zie de aanvulling hieronder.
  Opslagquota en latency blijven operationele grenzen, geen reden om bewijs te wissen.
- De twee al bestaande Microsoft-clienttestfouten blijven aanwezig; deze ronde
  verandert hun componenten niet.

Microsoft Graph v1.0 documenteert native conceptcreatie met `Mail.ReadWrite`, ook
voor persoonlijke Microsoft-accounts. `Mail.Send` is daarvoor niet nodig. Het
bestaande gekoppelde account wordt gebruikt; er is geen nieuwe onboarding nodig.
Of de huidige token de vereiste schrijfpermissie al heeft, is niet live onderzocht.

Officiële contracten:
- [createReply](https://learn.microsoft.com/en-us/graph/api/message-createreply?view=graph-rest-1.0)
- [createReplyAll](https://learn.microsoft.com/en-us/graph/api/message-createreplyall?view=graph-rest-1.0)
- [createForward](https://learn.microsoft.com/en-us/graph/api/message-createforward?view=graph-rest-1.0)

## Offline verificatie

- Microsoft-backend: 840 geslaagd, inclusief 28 nieuwe response-tests.
- Nieuwe React-interactietests: 10 geslaagd, inclusief de drie werkbalkknoppen,
  opslaan, heropenen/bewerken, dubbelklik, verloren browserantwoord en reconnect.
- Bestaande Send/Compose-interactietests: 32 + 21 geslaagd.
- Brede Microsoft-clientcontrole: 47 van 49 suites geslaagd. Bestaande fouten:
  `MicrosoftFilteredSenderAction.test.ts` verwacht een ontbrekende componenttag;
  `MicrosoftMailboxFolderRail.test.ts` verwacht `lg:flex` waar `xl:flex` staat.
- Auth/OAuth en Gmail OAuth: 185 tests in 5 suites geslaagd.
- Bestaande multiproces-Send/migratie- en lokale-autorisatieproeven: 3 geslaagd.
- TypeScript app/node en volledige frontend-build geslaagd. Alleen bestaande
  waarschuwingen over Browserslist-data en grote bundels.
- Gmail/IMAP-bronbeveiliging: 645 beschermde bronbestanden ongewijzigd; gedeelde API
  buiten het bestaande Microsoft-gebied behouden. `git diff --check` geslaagd.

Tests gebruikten uitsluitend fake Graph en geïsoleerde opslag. De runners wissen
geërfde credentials en blokkeren netwerkverkeer. Releaseflags worden uitsluitend
in de afzonderlijke testprocessen gemockt; de bronwaarden blijven false.
Logbestanden: `/private/tmp/cuevion-reply-*`.

## Aanvulling: Compose completion (26 oktober 2026)

### Duurzame Compose-opslag

De bestaande mailboxsleutel publiceert nu één v2-root met content-addressed,
onveranderlijke radixsegmenten voor request-ID, chronologische volgorde en actieve
blokkades. Dit gebruikt hetzelfde CAS-publicatiepatroon als Send in een eigen
Compose-namespace; Send-code of Send-opslag is niet gewijzigd in deze ronde.
De root blijft klein; 64 KiB is een segmentlimiet, geen mailboxquotum. Er is geen
limiet van 32 operaties meer. Geschiedenis, request-ID-bewijs en blokkades hebben
geen TTL, evictie of automatische verwijdering.

Een v1-root blijft leesbaar. De eerste mutatie bouwt de indexen, bewaart de volledige
oude root in een immutable archief en publiceert alles met één CAS tegen exact de
oude root. Mislukte segmentpublicatie laat de oude root intact. Een verloren root-ACK
verleent nooit Graph-mutatierecht. Voorheen ontbrekende root plus een duurzame
initialized-marker blokkeert; er is geen automatische reset naar lege geschiedenis.
Onbereikbare segmenten na een onderbreking blijven staan. Verwijderen of repareren
vereist later een afzonderlijk ontworpen operationele procedure.

Status gebruikt pagina's van 32 plus relevante oudere blokkades voor het huidige
concept, onzekere creates en de lopende request-ID. De UI kan oudere pagina's
opvragen. De volgorde van nieuwe records is een duurzame oplopende sequence.
V1-records hadden geen timestamps: hun bestaande geserialiseerde volgorde wordt
bij migratie vastgelegd, zonder achteraf een historische tijdsvolgorde te verzinnen.
Mailboxlease, eigenaar-/accountscope, generation-controle voor claims en CAS blijven
verplicht. Reconnect verwijdert geen historisch bewijs.

Opslaggebruik groeit met de geschiedenis en copy-on-write indexpaden. Er is geen
bewijs van onbeperkt providerquotum of gegarandeerde live latency. Quota, monitoring,
back-up/herstel en eventueel veilig opruimen van onbereikbare segmenten moeten vóór
langdurig Production-gebruik operationeel worden uitgewerkt. Opslagfouten blokkeren.

### Veilige basisopmaak

Het Microsoft-paneel hergebruikt de bestaande `desktopComposeBodyDraft`-buffer en
Gmail/IMAP-editorconventies. De gedeelde Gmail/IMAP-componenten zijn niet gewijzigd.
Formatting zet eigen tekst expliciet om naar HTML. De editor biedt vet, cursief,
onderstrepen, nummer-/opsommingslijsten en undo/redo; paste voegt uitsluitend tekst
toe. Actieve selectie moet binnen de eigen editor liggen. Quotes blijven daarbuiten.
De editor is tijdens opslag, reconnect of een blokkade niet bewerkbaar.

Client en server valideren een kleine HTML-allowlist. Geen scripts, events, CSS,
images/CID, SVG, embeds, onbekende attributen of actieve URL-schema's. Niet-ondersteunde
inhoud wordt geweigerd, niet stilzwijgend weggefilterd. B/i-aliases, HTML-entiteiten,
een lege html/body-envelop en de expliciete UTF-8 Content-Type-meta-envelop kunnen
veilig worden gecanonicaliseerd voor Compose. Willekeurige Graph-normalisatie niet.

Een generatie-onafhankelijke, accountgebonden CAS-marker identificeert gewone
HTML-concepten waarvoor deze flow bevoegd is. De marker bevat alleen versie/scope,
geen body. Oude tekstconcepten blijven via hun bestaande MIME/text-preferencebewijs
leesbaar. Oude responseprovenance blijft leesbaar; een tekstcomment kan opmaak
krijgen zonder de oorspronkelijke quotehash/conversatie of bijlagen te veranderen.
Complexe externe Outlook-HTML wordt niet automatisch als bewerkbare eigen HTML
geadopteerd. De bestaande HTML/CID-reader en headereditor blijven beschikbaar.

Compose leest de HTML-representatie onafhankelijk terug en controleert inhoud,
versie, bijlageninventaris en conversatie. Responsequotes blijven exact hashmatig
beschermd. Dit is geen atomaire bescherming tegen externe Outlook-wijzigingen en
geen HTML/MIME-bewijs voor Send. De bestaande strenge Send-reader en manifestcontrole
zijn niet versoepeld. Geformatteerde en responseconcepten blijven in Cuevion
verzendgeblokkeerd. Onzekerheid veroorzaakt geen automatische retry of ontgrendeling.

### Verificatie van deze aanvulling

- Microsoft-backend: 860 tests geslaagd, inclusief 20 nieuwe tests met onder meer
  120 opeenvolgende saves, alle v1-records, archivering, onderbroken publicatie,
  opslagfouten, verloren ACK, concurrentie, reconnect en HTML/quote-bescherming.
- Compose/Response/Send React-interactietests: 27 + 13 + 32 geslaagd.
- Microsoft-clientselectie: 47/49 suites geslaagd; de twee eerder genoemde fouten
  blijven ongewijzigd. Geen netwerkpogingen in de geïsoleerde runners.
- Auth/OAuth/Gmail OAuth: 185 tests geslaagd.
- Onafhankelijke serverprocessen: 2 nieuwe Compose-CAS/migratietests en 3 bestaande
  Send/lokale-autorisatietests geslaagd.
- TypeScript app/node, frontend-build en `git diff --check`: geslaagd.
- Gmail/IMAP-bronbeveiliging: 645 beschermde bestanden ongewijzigd.

Nog nodig voor daadwerkelijk gebruik: afzonderlijke beoordeling en activering van
bestaande Compose/releasecontroles, bevestiging van Mail.ReadWrite in de bestaande
onboarding, en een expliciet geautoriseerde eigen-accountacceptatietest van echte
Graph-HTML-normalisatie, quote-/bijlagebehoud, browserbewerking en opslaglatency.
Mail.Send is voor conceptbewerking niet nodig. Send van HTML/responses vereist nog
apart aantoonbaar inhoudsbewijs. Er zijn geen releasevlaggen, OAuth-instellingen of
Production-configuraties gewijzigd en geen echte Graph-mutaties uitgevoerd.

## First live acceptance preparation — bestaande Cuevion-app

Voorbereid op 26 oktober 2026; geen vrijgave of live conceptmutatie uitgevoerd.
De oude lokale testbasis is hiervoor niet nodig. Bestaande Auth0, Microsoft-app,
onboarding, redirect en opslag worden hergebruikt.

### Feitelijk vastgesteld en nog onbekend

- Vercel-project `cuevion`, Production `app.cuevion.com`, is READY op commit
  `d61c33d90a7b10cb17e5827c6c19991f6b543dfa`, deployment
  `dpl_AarQXDRb57scSMNdAZbLVMSDiAb5`. Dit is ook de via `git ls-remote` waargenomen
  actuele main. De werkbranch HEAD `6522ff36982f98be23ced77652247ec57db7f7ac`
  heeft deze main als ancestor en bevat vier aanvullende commits. Er is daarnaast
  veel ongecommitte en untracked implementatie; alleen HEAD uitrollen mist die code.
- De bestaande Microsoft OAuth-configuratie is als Production-metadata aanwezig.
  De callback blijft `https://app.cuevion.com/api/inboxes/oauth-callback`.
- De huidige OAuth-code vraagt al User.Read, Mail.Read, Mail.ReadWrite en
  offline_access aan, naast de OIDC-scopes. Mail.Send wordt niet aangevraagd.
  Dit bewijst niet dat het specifieke bestaande token Mail.ReadWrite heeft.
- Alleen-lezen uitlezen via lokale Vercel CLI-credentials gaf HTTP 403 `forbidden`.
  De werkelijke scope, eigenaar-/workspace-ID, account-ID, mailbox-ID en actuele
  opslagquota/latency zijn daarom NIET als live bevestigd aangemerkt. Geen token
  is afgedrukt, vernieuwd of aan Graph aangeboden.
- De Vercel-metadata bevat bestaande KV-configuratie voor Production, Preview en
  Development. Deze voorbereiding verandert die niet en voert geen live CAS-test
  uit. Offline tests gebruiken uitsluitend geïsoleerde opslag.

### Wat nu is voorbereid

`microsoft_draft_acceptance.py` biedt een aparte beperkte Production-toelating,
zonder de algemene Compose/Response/Send-releasevlaggen in te schakelen. De nieuwe
policy ontbreekt standaard. Ontbrekende, onleesbare of ongeldige policy/control
blokkeert. Er is geen publiek endpoint om de policy, noodstop of limiet te wijzigen.
Alle bediening daarvan vereist operatorrechten op de bestaande CAS-opslag.

De policy bindt exact Auth0-eigenaar (ownerUserId én ownerEmail), workspace,
Microsoft-account-ID, interne mailbox-ID en het hard vastgelegde adres
`carltricks@live.nl`. Het e-mailadres alleen verleent geen bevoegdheid. De bestaande
server-ownerresolver blijft verplicht. Voor een mutatie worden actuele tokenrechten
en de daadwerkelijke Graph /me-identiteit gecontroleerd. Reconnect naar een ander
account wordt geweigerd. De tijdelijke toelating werkt alleen op de bestaande
canonieke Production-origin en met de bestaande juiste OAuth-redirect.

Alleen nieuw concept, opslaan, heropenen, bewerken, Reply-concept en Forward-concept
zijn bereikbaar. Alleen concepten die binnen het testvenster zijn aangemaakt mogen
worden bijgewerkt. Reply/Forward mogen maximaal twee vooraf gekozen immutable
bronbericht-ID's gebruiken. De eerste test is zonder bijlagen; ook bestaande
bijlagen in een bronconcept/bronbericht blokkeren de beperkte schrijfroute.
Reply All, bijlagebewerkingen en alle Send-routes blijven buiten deze toelating.

Een venster duurt maximaal 1.800 seconden en consumeert maximaal 12 duurzame
Compose-operaties vanaf een gecontroleerde baselineCount. CAS, mailboxlease,
Send-index, onzekere Compose-operaties, inhoudscontrole, quoteprovenance en
credential-generationcontrole blijven actief. Geen automatische retries.

De duurzame control bevat `stopped`. Elke mutatie controleert de actuele control
opnieuw onder de lease, zo dicht mogelijk vóór Graph; native createReply/Forward
en de aansluitende PATCH hebben afzonderlijke checkpoints. Stop verwijdert geen
historie. Read-only status blijft beschikbaar zolang de identiteits-policy
behouden blijft. Een reeds verzonden HTTP-verzoek kan niet worden teruggeroepen;
een stop is geen atomaire annulering van een in-flight Graph-verzoek. Bij een
onzekere uitkomst volgt geen tweede poging of automatische tweede PATCH.

De bestaande interface heeft een expliciete **Check Microsoft draft permissions**-
knop. Die doet één geauthenticeerde same-origin `capabilities`-read via de bestaande
Compose-route. Geen Graph, tokenrefresh, claim of mailboxmutatie. Het antwoord
onderscheidt het opgeslagen Mail.ReadWrite-grant van tokenverval en retourneert de
werkelijke serveridentiteit, zonder credentials. Alleen een passende serverpolicy
kan de interface voor de gecontroleerde test vrijgeven. Clienthints worden niet
als serverautorisatie meegestuurd/geaccepteerd.

### Operatorconfiguratie — niet uitgevoerd

Policy-key: `cuevion:microsoft:draft-acceptance-policy:v1`.
Vul uitsluitend geverifieerde serverwaarden in, nooit op basis van alleen het adres:

```json
{
  "v": 1,
  "ownerUserId": "<actual owner user ID>",
  "workspaceId": "<actual workspace ID>",
  "ownerEmail": "<actual normalized Cuevion owner email>",
  "accountId": "<actual Microsoft Graph account ID>",
  "mailboxId": "<actual Cuevion mailbox ID>",
  "mailboxAddress": "carltricks@live.nl"
}
```

De bovenstaande placeholders zijn bewust geen werkende configuratie.
Bereken policyHash met de bestaande `microsoft_draft_attachment_snapshot.digest`.
Control-key: `cuevion:microsoft:draft-acceptance:v1:` gevolgd door die policyHash.
De control heeft exact v=1, policyHash, stopped, notBefore, expiresAt,
baselineCount, maxOperations en sourceIds. Maak deze aanvankelijk `stopped: true`.
notBefore/expiresAt zijn UTC Unix-seconden; maxOperations is hoogstens 12;
sourceIds bevat hoogstens twee afzonderlijk goedgekeurde immutable bron-ID's.
baselineCount komt uit de actuele Compose-root: v2 count of het aantal v1-records.
Bewaar policy/control zonder TTL; het tijdvenster is een afzonderlijke controle.
Lees bestaande records eerst en gebruik de bestaande CAS-adapter, geen blind SET.

De duurzame stop is het normale onmiddellijke stopmechanisme. De optionele
servervariabele `CUEVION_MICROSOFT_DRAFT_ACCEPTANCE_STOP` kan aanvullend niet-`0`
zijn om te blokkeren, maar een Vercel-envwijziging is geen onmiddellijk runtime-
stopmechanisme: daarvoor kan een nieuwe deployment nodig zijn. Gebruik de control.
Geen van deze waarden is in deze ronde in Production geschreven of geactiveerd.

### Eén gecontroleerde deployment — later afzonderlijk goed te keuren

1. Beoordeel de volledige werkboom inclusief untracked bestanden; leg alle nodige
   wijzigingen vast in één reviewbare release. Controleer opnieuw de actuele main.
   De nu waargenomen main is ancestor, maar dit kan voor een latere merge veranderen.
2. Voer de offline tests en build op die exacte release uit. Controleer in de
   bestaande KV-console dat de nieuwe policy ontbreekt of de passende control
   veilig gestopt is. Bevestig quota, duurzame CAS/EVAL-ondersteuning, geen TTL of
   evictie op bewijs, back-up/herstel en voldoende latencyruimte. Geen Graph-write
   gebruiken als opslagproef. Controleer lease- en foutmeldingen.
3. Laat Rutger de commit/push/merge en één Production-deployment expliciet
   goedkeuren. Gebruik de bestaande main -> Vercel-koppeling eenmaal. Vermijd
   een aparte feature-branch-push/Preview-build als die onnodig buildbudget kost.
   Geen nieuwe Auth0-app, redirect, Microsoft-app of testinfrastructuur nodig.
4. Houd de algemene releasevlaggen OFF en de policy afwezig/gestopt. Controleer na
   de deployment login en bestaande Microsoft/Gmail/IMAP-leesfuncties zonder
   mailboxmutaties. Gebruik de nieuwe rechtenknop voor de werkelijke scope en IDs.
5. Indien Mail.ReadWrite ontbreekt: Rutger bevestigt uitsluitend voor deze bestaande
   mailbox een reconnect via de bestaande Cuevion-onboarding. De bestaande OAuth-
   scopekeuze is geschikt; geen algemene scopewijziging en geen Mail.Send. Controleer
   daarna account/workspace/mailbox opnieuw. Een opgeslagen grant is geen bewijs
   dat een verlopen of ingetrokken token nog geldig is; de normale tokenroute
   valideert dit opnieuw vóór een echte operatie.
6. Kies één/twee onbelangrijke bronberichten zonder bijlagen, leg hun immutable IDs
   vast en inspecteer bestaande onzekerheden. Schrijf pas na expliciete toestemming
   de policy/control voor de vastgestelde eigenaar en mailbox. Laat deze gestopt.
   Bevestig dat andere mailboxen geen toelating krijgen en dat status leesbaar is.
7. Pas na afzonderlijke toestemming voor live conceptmutaties: activeer via CAS één
   kort venster. Maak één nieuw concept, sla op, heropen, wijzig éénmaal; maak daarna
   één Reply- en één Forward-concept. Controleer elke bevestiging en inhoud vóór de
   volgende handeling. Begin met platte tekst; complexere HTML pas na afzonderlijke
   inhoudscontrole. Stuur niets en schakel geen Send-vlag in.
8. Zet na afloop `stopped: true` via CAS en lees de gestopte control terug. Bewaar
   ledgers, policy, control en resultaten. Geen automatische cleanup of unlock.

Bij timeout, onzekere mutatie, afwijkende quote/body, ontbrekende leesbevestiging,
MIME/HTML-onzekerheid of opslagfout: stop de control, noteer request-ID, uitsluitend
status lezen, niet opnieuw opslaan of een verse request-ID proberen. Een mogelijk
achtergelaten quote-only concept blijft beschermd. Niet handmatig wissen als
onderdeel van herstel zonder afzonderlijke beoordeling.

### Rollback

Vóór enige live conceptmutatie is de bestaande READY-deployment hierboven een
waargenomen rollbackkandidaat. Noteer die ID vlak voor de deployment opnieuw.
Na live mutaties is **control stoppen en de huidige ledgerbewuste code behouden**
de primaire terugval. Een oudere deployment kent mogelijk de nieuwe ledgers niet;
blind downgraden is dan geen bewezen veilig herstel. Graph-mutaties worden door
Vercel-rollback nooit teruggedraaid. Bewijs/indices mogen nooit worden gewist.

### Verificatie en oordeel

- Microsoft-backend: 877 geslaagd, inclusief 17 nieuwe acceptance-tests.
- Auth/OAuth/Gmail OAuth: 185 geslaagd. Geen onboardingcode gewijzigd.
- Compose/Response/Send-clientinteracties: 29 + 15 + 32 geslaagd.
- Volledige Microsoft-clientregressie: 49/49 suites geslaagd, nul netwerkpogingen.
- Afzonderlijke serverprocessen: 5/5 concurrency-, migratie- en herstarttests geslaagd.
- De twee oude statische Microsoft-clientverwachtingen zijn na inspectie aangepast
  aan de al bestaande Learning-component en het al bestaande xl-breakpoint; de
  AST-bereikbaarheidscontrole en verbod op mailboxmutaties zijn behouden.
- TypeScript app/node via volledige build geslaagd; bestaande Browserslist- en
  bundelgroottewaarschuwingen blijven. Gmail/IMAP-freeze: 645 bestanden ongewijzigd.
- Alle nieuwe Graph-proeven gebruiken fakes en geïsoleerde CAS. Geen echte Graph-
  verzoeken, e-mails, Production-writes, consentactivatie, commits, pushes of deploys.

GO voor de lokaal afgeronde voorbereiding en verdere releasereview.
NO-GO om nu live conceptmutaties te starten: echte scope/identiteit, quota/latency,
operationele stopbevoegdheid, release van de volledige werkboom en de afzonderlijke
accountgebonden toestemming zijn nog niet live bevestigd of verleend.
