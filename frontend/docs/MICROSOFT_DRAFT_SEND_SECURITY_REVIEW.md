# Microsoft Send — materiaal voor afzonderlijke beveiligingscontrole

Bijgewerkt: 26 oktober 2026 (Security Fixes 1, 2, 3 en 4). Branch: `codex/microsoft-drafts-recovery`.
Compose-checkpoint: `6522ff36982f98be23ced77652247ec57db7f7ac`.
Alle Send-wijzigingen na dit checkpoint zijn ongecommit. Geen push of deployment.

## Afbakening

De nieuwe route verzendt uitsluitend een bestaand tekstconcept uit de nieuwe
Microsoft Compose-interface. Ze maakt geen concept aan, wijzigt geen bericht,
uploadt geen bestanden en sluit Reply, Reply All en Forward niet aan.
De frontendvlag `MICROSOFT_DRAFT_SEND_RELEASED` en backendvlag
`_DRAFT_SEND_RELEASED` blijven `false`/`False`; Production wordt onafhankelijk
geweigerd, ook bij een in-memory testoverride van de bronvlag.

## Controles vóór de daadwerkelijke provideractie

De HTTP-grens controleert methode, canonical Origin/Host, berichtgrootte,
dubbele JSON-sleutels en het exacte schema. De service controleert eigenaar,
mailbox, credential-generatie, Mail.ReadWrite, Mail.Send en de mailboxlease.
Herhaalde complete momentopnamen controleren exacte Drafts-mapidentiteit, isDraft,
bericht-ID, ETag, inhoud, ontvangers inclusief Cc/Bcc en de bijlageninventaris.
De ETag en fingerprint moeten overeenkomen met het door de composer geladen
Graph-concept. Lege ontvangers, onderwerp of berichttekst worden geweigerd.

Onzekere Compose-, batchbijlagen- en oudere per-bestandbijlagenoperaties
blokkeren Send. Omgekeerd blokkeren lopende/onzekere Send-operaties de nieuwe
Compose-editor, de bestaande Drafts-editor en de bijlagenadapter. De interface
staat Send alleen toe zonder onopgeslagen tekst of lokaal geselecteerde bestanden.
Alle bestanden moeten eerst met de bestaande veilige adapter zijn opgeslagen.

## Duurzaamheid en maximaal één Send

De bestaande outbound-ledger blijft gezaghebbend voor de uitvoering. Een
duurzame, gesegmenteerde index koppelt mailbox, concept en attempt-ID voor herstel
na herladen. De index wordt vóór de outbound-reservering geschreven. Ontbrekende
outbound-opslag na een verloren indexbevestiging is onzeker en blijft geblokkeerd.
Een ander attempt-ID kan een eerdere onzekere of bevestigde verzending van
hetzelfde concept niet omzeilen. CAS en de mailboxlease beschermen concurrency.

De outbound-overgang naar `send_started` wordt duurzaam bevestigd vóór de ene
Graph-sendaanroep. Een verloren start-CAS-bevestiging blijft onzeker, zelfs als
het transport daardoor nooit is aangeroepen. Replays retourneren alleen de
bewaarde status en voeren geen provideractie uit. Er zijn geen automatische
retries, timers die opnieuw verzenden of automatische vervangende attempt-ID's.

## Afzender en duurzaam goedgekeurd inhoudsmanifest (Fixes 2)

`from`, `sender` en `replyTo` zijn opgenomen in de beveiligde Drafts-snapshot,
de inhoudshash en de fingerprint. Voor iedere nieuwe verzending wordt met de
actuele token `/me` gecontroleerd: zowel Graph-account-ID als `mail` moeten exact
bij de geverifieerde eigen mailbox horen. `from` én `sender` moeten datzelfde
adres bevatten. Reply-To mag leeg zijn of uitsluitend dat eigen adres bevatten.
Geen aliasresolutie, ontbrekende-waarde-default, gedelegeerd verzenden of Send As.
Een onbekende configuratie blokkeert de verzending zonder het concept te wijzigen.
Dit beleid volgt het onderscheid tussen [from en sender in Graph](https://learn.microsoft.com/en-us/graph/api/resources/message).

Vóór `send_started` wordt een apart manifest duurzaam met CAS opgeslagen en
teruggelezen. Het is gebonden aan eigenaar, mailbox, Microsoft-account, attempt,
concept en de oorspronkelijke goedgekeurde snapshotbinding. Het bevat hashes van
Aan/Cc/Bcc, from/sender/Reply-To, onderwerp en volledige body inclusief bodytype.
Per bijlage bewaart het een hash van de metadata, de echte bytelengte en SHA-256
van alle gedecodeerde bytes. Adressen, namen, onderwerpen, berichtteksten,
credentials en bijlagebytes worden niet toegevoegd aan logs of ledgers.
Hashes blijven privacygevoelige afgeleide gegevens; dit is geen anonimiteitsclaim.

De originele ETag/fingerprint uit de composer moet nog kloppen. De byte-inhoud
wordt voor het manifest volledig opgehaald, herhaald geobserveerd en na duurzame
opslag opnieuw gecontroleerd vóór de verzendclaim. Daarmee wordt ook vervanging
met dezelfde naam en grootte tussen die waarnemingen gedetecteerd. De bytes zijn
serverwaarnemingen bij Send; de eerdere composerfingerprint bindt bijlagen aan
Graph-ID/versie/metadata, en is op zichzelf geen eerder door de gebruiker gezien
bytebewijs. De provider-versiecontrole en de beperkingen van externe races blijven
onderdeel van het vrijgavebesluit.

Voorlopig worden alleen gewone, niet-inline `fileAttachment`-bijlagen zonder
Content-ID ondersteund: maximaal 10, maximaal 2 MiB per bestand en 8 MiB aan
werkelijke bytes totaal. Zowel inventaris als detailrespons moeten het concrete
type en de metadata bevestigen; `contentBytes` wordt strikt als base64 gedecodeerd.
Een ontbrekende/ongeldige bytebron, onvolledige pagina, dubbele ID, itemAttachment,
referenceAttachment, onbekend type of overschrijding blokkeert Send. Geen bijlage
wordt daarvoor verwijderd of aangepast. Graph documenteert [fileAttachment en
contentBytes](https://learn.microsoft.com/en-us/graph/api/resources/fileattachment)
en de verschillende [bijlagentypen](https://learn.microsoft.com/en-us/graph/api/attachment-get).
Dit bewijst het gedrag van onze fake-Graph-integratie; live roundtripgedrag blijft
ongetest.

Na Send wordt dezelfde oorspronkelijke immutable bericht-ID onafhankelijk uit
Sent Items gelezen, met controle van isDraft=false, sentDateTime en mapidentiteit.
Volledige inhoud en bijlagebytes worden opnieuw in herhaalde waarnemingen gelezen
en met het opgeslagen manifest vergeleken. Geen zoekactie op onderwerp of tijd.
JSON-veldnamenvolgorde en bijlagevolgorde zijn niet inhoudelijk; bijlagen worden
als volledige multiset vergeleken, inclusief duplicaten. Attachment-ID's binden
individuele GETs maar zijn geen inhoud; een gewijzigde ID met exact dezelfde
metadata en bytes verandert die inhoud niet. Alle berichtveldwaarden worden
verder exact vergeleken, inclusief ontvangerordering, display names, adrescasing,
bodytype, whitespace en HTML. Geen onbewezen tekst/HTML- of aliasgelijkwaardigheid.
Een provider-normalisatie kan daarom conservatief als afwijking worden gemeld;
ontbrekende of niet verifieerbare eigenschappen blijven onzeker.

## Betekenis van de statussen

| Uitkomst | Vereist bewijs |
| --- | --- |
| `confirmed_performed` | Exacte Sent-identiteit én alle inhoud/bytehashes komen overeen met het vooraf opgeslagen manifest; beide bewijzen zijn duurzaam vastgelegd. |
| `performed_content_mismatch` | Exacte Sent-kopie is onafhankelijk gevonden, maar de volledige verifieerbare inhoud wijkt af van het manifest. Blijvend geblokkeerd; nooit automatisch opnieuw verzenden. |
| `confirmed_not_started` | Een terminale outbound-CAS vóór mogelijke provideruitvoering; deze worker en replays kunnen daarna niet meer starten. |
| `uncertain` | Alle overige situaties, waaronder gereserveerd/voorbereid, mogelijk gestart, verloren antwoorden en 202 zonder inhoudsbewijs, ontbrekende/vertraagde kopie, onleesbare inhoud en historische Sent-identiteit zonder manifest. |

`providerAccepted` is afzonderlijk zichtbaar. `deliveryConfirmed` blijft altijd
`false`. `sentCopyConfirmed` bewijst uitsluitend exacte Sent-identiteit: ook een
afwijking of een oude receipt zonder inhoudsmanifest kan dit veld op true hebben.
Alleen `confirmed_performed` betekent inhoudelijk bevestigd. De implementatie zoekt nooit op alleen onderwerp, ontvangers of tijd.
Microsoft documenteert afzonderlijke conceptverzending en een 202-respons in
[message: send](https://learn.microsoft.com/en-us/graph/api/message-send), en het
terugvinden van dezelfde ID in Sent Items in
[Immutable IDs](https://learn.microsoft.com/en-us/graph/outlook-immutable-id).

De status blijft bij herladen beschikbaar. Ook een nieuw Compose-venster toont
recente mailboxverzendingen, zodat een inmiddels uit Drafts verdwenen bericht
zijn onzekere status niet verbergt. Expliciete statuscontrole doet uitsluitend
provider-GETs; ze kan alleen positief Sent-bewijs in de ledger vastleggen.
Credentials en bijlage-inhoud worden niet in browseropslag bewaard. De index
bevat alleen ID's, hashes en beperkte foutcodes.

## Open punten vóór vrijgave

**De laatste Graph-read en Graph-send vormen geen bewezen atomische
versiecontrole.** Microsoft documenteert voor de sendactie geen If-Match-contract.
Een Outlook-wijziging na de laatste observatie kan daarom niet volledig worden
uitgesloten. De huidige implementatie claimt die garantie niet. Dit is een
expliciet punt voor de afzonderlijke beveiligingscontrole en een vrijgavebesluit;
alle bronvlaggen blijven uit. De manifestopslag vindt vóór de laatste volledige
inhoudscontrole plaats om geen extra opslagstap in het laatste read/send-interval
te introduceren. De noodzakelijke lease-, eigenaars- en duurzame startcontroles
blijven bestaan. Achterafverificatie detecteert een afwijkende Sent-kopie, maar
voorkomt niet dat afwijkende inhoud al is verzonden. Ook herhaalde GETs zijn geen
atomaire snapshot en sluiten wijziging-en-herstel tussen waarnemingen niet uit.
Het bewijs gaat over de waargenomen Sent-kopie, niet over aflevering, ontvangers
hun ontvangen MIME of een onveranderlijk forensisch archief. Een header zonder bewezen providersemantiek is
niet als schijnbeveiliging toegevoegd.

Fixes 3 vervangt de mailboxgrens van 32 pogingen/64 KiB door permanente
opslagsegmentatie; zie het ontwerp hieronder. Er is geen TTL, eviction of
automatische verwijdering. Operationeel herstel van werkelijk onzekere pogingen
blijft een afzonderlijk vrijgavepunt. Een reconnect wist oude blokkades niet;
de huidige eigenaar/mailbox/account moeten bij de historische index passen. Een
gewijzigde credential-generatie wist bewijs niet. Nieuwe credentials kunnen alleen
een nieuwe GET-verificatie uitvoeren, inclusief een nieuwe `/me`-identiteitscheck.
Oude identity-only bevestigingen zonder voorafgaand manifest blijven inhoudelijk
onzeker; er wordt nooit achteraf een goedkeuringsmanifest geconstrueerd.
Er is geen geforceerde ontgrendeling of herverzendknop voor onzekere uitkomsten.
Live Graph-acceptatie is niet uitgevoerd. HTML/CID-conceptbewerking blijft buiten
de nieuwe tekstcompositor; bestaande beperkingen blijven gelden.

## Offline verificatie na Fixes 2

- Microsoft-backendrunner: 750 tests geslaagd (30 extra in Fixes 2), geen externe I/O-pogingen.
- Send-interacties: 26/26 geslaagd (4 extra in Fixes 2); Compose-interacties: 19/19.
- Brede clientrunner: 133/138 suites geslaagd; dezelfde vijf bestaande fouten.
- Microsoft-subset: 46/48 suites geslaagd; dezelfde twee bestaande fouten.
- Auth/OAuth: 185 geselecteerde regressietests geslaagd. Brede Python-run: 23/24 suites; 866 tests, dezelfde 74 failures en 13 errors in de bestaande Gmail-ownershipsuite.
- TypeScript en volledige frontend-build geslaagd. Bestaande Browserslist-/chunkgroottewaarschuwingen blijven zichtbaar.
- Bronbeveiliging: 645 beschermde Gmail/IMAP/shared-bestanden ongewijzigd.
- `git diff --check` geslaagd.

De vijf bestaande clientfouten zijn `App.collaborationGuestRoute.test.ts`,
`MicrosoftFilteredSenderAction.test.ts`, `MicrosoftMailboxFolderRail.test.ts`,
`inboxConnectionApi.gmailOwnership.test.ts` en `mailboxDisplayName.test.ts`.
De twee Microsoft-fouten waren al vóór de checkpointcommit aanwezig.
Logs en per-suite JSON voor deze ronde:
`/private/tmp/cuevion-ms-fixes2-backend-final.log`,
`/private/tmp/cuevion-ms-fixes2-client-final/`,
`/private/tmp/cuevion-ms-fixes2-python/`,
`/private/tmp/cuevion-ms-fixes2-typescript.log` en
`/private/tmp/cuevion-ms-fixes2-build.log`.
De falende suites zijn vergeleken met de bewaarde Fixes 1-baseline; de 87
Python-foutidentiteiten zijn gelijk. Geen falende suite toegevoegd door deze ronde.

De nieuwe fake tests dekken ontvanger/onderwerp/body/from/sender/Reply-To-wijzigingen
op de verzendgrens, vervangen bytes bij gelijke naam/grootte, ontbrekende of extra
bijlagen, onbekende typen, onvolledige inventaris, vertraagde/ontbrekende Sent,
herladen/reconnect na een afwijking, verkeerde actuele `/me`-identiteit, verloren
CAS-antwoorden, herstel tussen beide bewijscommits, dubbele verzendverzoeken en
gelijktijdige (ook tegenstrijdige) statusbewijzen. Een afwijking is absorberend.
De bestaande mutatieblokkades, flag-uit-scenario's en legacy fail-closed-route
blijven getest.

## Vrijgaveoordeel na Fixes 2

De gevraagde lokale afzender- en inhoudscontroles zijn geïmplementeerd en offline
getest. Production blijft geblokkeerd. Nog noodzakelijk: expliciet beleid voor
het niet-atomaire Outlook-racevenster en voor afwijkende/onzekere uitkomsten;
retentie en herstel rond de 32-pogingenlimiet; beoordeling van de bestaande
regressiefouten; en afzonderlijk geautoriseerde live-validatie van accountvelden,
Graph-bodyrepresentatie, bijlagemetadata/bytes en vertraagde exacte Sent-kopieën.
Geen live-validatie of vrijgave is onderdeel van deze ronde. Reply/Reply All/
Forward en hun bestaande flows zijn niet aangepast.

Uitsluitend fake Graph, geïsoleerde CAS-opslag en netwerkblokkerende runners.
Geen echte e-mails, mailboxmutaties, wijziging van Gmail/IMAP, GitHub-push,
Vercel-deployment of wijziging aan Production.


## Security Fixes 3 — permanente historie en zichtbare blokkades

Datum: 26 oktober 2026. Alle wijzigingen lokaal op
`codex/microsoft-drafts-recovery`; geen commit, push of deployment.

### Nieuwe opslagarchitectuur

De limieten van 32 pogingen en 64 KiB waren mailboxbrede grenzen in één JSON-index.
Ze zijn vervangen door een kleine v2-mailboxroot op dezelfde duurzame sleutel,
met drie verwijzingen naar onveranderlijke radixboomsegmenten: request-ID naar
poging, concept-ID naar blokkerende/laatste poging, en volgnummer naar request-ID.
Alle drie de verwijzingen en de teller worden met **één root-CAS** gepubliceerd.
De bestaande outbound-ledger en inhoudsmanifesten blijven afzonderlijk bewaard.

Een segment is aan de eigenaar/mailbox/account-scope gebonden en heeft een
inhoudsadres (SHA-256). Iedere gelezen verwijzing wordt op inhoud, schema, scope
en boomprefix gecontroleerd. De grens van 64 KiB geldt per opslagrecord, niet
voor de totale mailboxhistorie. Een nieuwe poging herschrijft alleen de benodigde
boompaden; dubbele-request- en conceptcontroles doorlopen geen volledige historie.
Boompaden zijn maximaal 64 niveaus. De teller gebruikt een exact interoperabel
integerbereik tot 2^53-1, geen praktisch mailboxquotum van 32 of 100 pogingen.
Dit is schaalbare segmentatie, geen claim van oneindige fysieke opslag.

**Geen enkel archief, bewijsrecord of segment wordt automatisch verwijderd.**
Ook oude versies en verweesde segmenten na een onderbreking blijven bewaard.
Een definitief niet-gestarte poging mag een nieuwe poging met een nieuwe ID
voor hetzelfde concept toestaan; de oude ID blijft permanent in de pogingindex
staan en kan nooit opnieuw een Graph-send claimen. Onzekere, afwijkende en
bevestigd uitgevoerde pogingen blijven conceptblokkades. Leeftijd geeft nooit
recht op ontgrendeling.

### Migratie, concurrency en fouten

Bestaande v1-records blijven zonder migratie leesbaar. De eerste nieuwe reserve
bouwt onder de bestaande mailboxlease eerst de nieuwe segmenten op en bewaart
de oorspronkelijke v1-index als onveranderlijk archief. Pas daarna publiceert
één CAS de volledige nieuwe root. Status-GETs migreren niets. Geen verandering
van historische credential-scope, outbound-fase of inhoudsbewijs.

Een onderbreking vóór de root-CAS laat de oude index intact. Een verloren
root-CAS-bevestiging verleent geen uitvoeringsrecht: de gepubliceerde poging is
bij herladen onzeker totdat uitvoeringsbewijs bestaat. Replays verzenden nooit.
Gelijktijdige schrijvers kunnen dezelfde onveranderlijke segmenten veilig
hergebruiken; uitsluitend de winnaar van de root-CAS publiceert zijn claim.
De mailboxlease en alle bestaande eigenaar-, mailbox-, generatie-, snapshot-,
manifest- en operatiecontroles blijven daarnaast gelden.

Een permanente initialisatiemarkering voorkomt dat een verdwenen v2-root als
lege historie wordt behandeld. Onderbreking bij de allereerste initialisatie
kan conservatief blokkeren; er is geen automatische reset of ontgrendeling.
Onleesbare of ontbrekende benodigde segmenten, archieven, roots of bewijsrecords
leiden tot blokkering, nooit tot een lege fallback. Dit veronderstelt duurzame,
lineair consistente CAS-opslag zonder externe TTL, eviction of rollback. Niet
ieder onafhankelijk boomsegment wordt bij iedere nieuwe aanvraag globaal gescand;
alle voor die aanvraag noodzakelijke bewijspaden worden gecontroleerd.

### Compose en chronologie

De nieuwe alleen-lezen `history`-actie levert pagina's van 32 records, met een
strikt aflopende cursor. Oudere pagina's blijven bereikbaar via **Load older send
history**; reeds geladen records worden niet weggegooid. De blokkade voor het
geopende concept wordt apart meegeleverd, ook wanneer die buiten de huidige
pagina valt. Een ontbrekende oudere pagina of opslagfout ontgrendelt de editor
niet. Een reeds bekende inhoudsafwijking kan niet door een verouderd antwoord
worden vervangen door een bevestiging.

Nieuwe pogingen krijgen een UTC-tijd en een monotoon CAS-volgnummer; de interface
toont nieuwste reservaties eerst, onafhankelijk van willekeurige request-ID's
en eventuele serverklokverschillen. v1 bevatte geen aanmaaktijd of betrouwbare
aanmaakvolgorde. Die oude records worden stabiel gemigreerd, met expliciet
**Original time unavailable**; hun oorspronkelijke chronologie is niet achteraf
bewijsbaar. Er wordt geen tijd verzonnen uit een request-ID of wijzigingstijd.

De vier inhoudelijke statussen blijven zichtbaar, inclusief het onderscheid
tussen exact bevestigde inhoud, afwijkende Sent-inhoud, onzekerheid en bevestigd
niet gestart. Herladen en reconnect laden de historie opnieuw; uitgeschakelde
Send-vlaggen verwijderen de statusweergave of blokkades niet. Geen geforceerde
ontgrendeling, handmatige retry of automatische verzendherhaling toegevoegd.
De oude ongepagineerde statusactie weigert grotere histories veilig, zodat oude
clients nooit stilzwijgend een onvolledige lijst als volledig kunnen behandelen.

### Verificatie na Fixes 3

- Microsoft-backend: **769/769** geslaagd, inclusief 19 nieuwe historietests.
- Afzonderlijke echte Python-processen: **2/2** geslaagd, met uitsluitend een
  tijdelijke SQLite-CAS, fake mailboxlease en fake Graph. Twee migratieprocessen
  publiceren één volledige root; twee verzendprocessen veroorzaken één send.
- **120 opeenvolgende geldige fake-Graph-verzendingen**, ruim voorbij beide oude
  grenzen, met behoud van de oudste onzekere en afwijkende poging.
- Daarnaast 105 definitief niet-gestarte pogingen gevolgd door een geldige send;
  oude IDs blijven receipt-only. Migratie van een volle v1-index, verloren ACKs,
  onderbroken segment-/rootopslag, ontbrekende en corrupte opslag, reconnect,
  clock skew en begrensde lookupkosten zijn getest.
- Send-client **31/31**, Compose-client **19/19**. Onder andere 105 vindbare
  records, alle oudere blokkades via pagina's, aparte oude conceptblokkade,
  herladen, opslagfouten, ongeldige cursors en verouderde afwijkingsstatus.
- Brede clientrun **133/138**; Microsoft-subset **46/48**. Dezelfde vijf bestaande
  falende suites als vóór deze ronde, geen nieuwe falende suite.
- Auth/OAuth **185/185**. Brede Python-run **23/24 suites**, 866 tests; dezelfde
  74 failures en 13 errors in `test_gmail_ownership_hardening`, met dezelfde
  87 foutidentiteiten als de bewaarde Fixes 2-baseline.
- TypeScript en volledige frontend-build geslaagd. De eerste TypeScript-run vond
  twee niet-ondersteunde Array.at-aanroepen; die zijn door gewone indexering
  vervangen, zonder wijziging van het compilatiedoel. Controle en build herhaald.
- Gmail/IMAP-bronbeveiliging: **645 beschermde bestanden ongewijzigd**;
  gedeelde API buiten het Microsoft-deel behouden. `git diff --check` geslaagd.

Bewijsbestanden: `/private/tmp/cuevion-ms-fixes3-backend-final.log`,
`/private/tmp/cuevion-ms-fixes3-multiprocess-final.log`,
`/private/tmp/cuevion-ms-fixes3-client-final/`,
`/private/tmp/cuevion-ms-fixes3-client-focused-final/`,
`/private/tmp/cuevion-ms-fixes3-python-final/`,
`/private/tmp/cuevion-ms-fixes3-typescript-final.log` en
`/private/tmp/cuevion-ms-fixes3-build-final.log`.

### Vrijgaveoordeel na Fixes 3

De oorspronkelijke Send-capaciteitsgrens en het verbergen van oude blokkades
zijn opgelost. Onzekerheid en afwijkingen worden permanent bewaard. Production
blijft geblokkeerd door het niet-atomaire Outlook-racevenster, ontbrekende
live-validatie van de Graph-roundtrip en beleid/operationeel herstel voor
onzekere of afwijkende uitkomsten. Ook opslaglatency, groei, quota, back-up en
herstel zonder verlies/rollback van verzendbewijzen moeten voor Production
worden gevalideerd. Deze ronde bewijst lokale algoritmische veiligheid onder
de genoemde opslagvoorwaarden, niet de beschikbaarheid van een live datastore.
Bestaande regressiefouten blijven afzonderlijk te beoordelen.

Alle releasevlaggen en Mail.Send-toestemming zijn ongewijzigd. Geen echte
Graph-mutaties, e-mails, Gmail/IMAP- of Reply/Reply All/Forward-wijzigingen,
commits, pushes, deployments of Production-aanpassingen.

## Security Fixes 4 — Inbox-mutaties en Graph-bodycontract

Uitgevoerd op 26 oktober 2026, uitsluitend lokaal op
`codex/microsoft-drafts-recovery`. Deze sectie beschrijft het actuele contract;
oudere bodybeschrijvingen hierboven horen bij de eerdere rondes.

### 1. Maponafhankelijke bescherming

Read/unread, Inbox -> Archive en Inbox -> Deleted Items raadplegen nu dezelfde
permanente Send-index als de bestaande conceptmutaties. De controle gebruikt de
exacte immutable bericht-ID en actuele mailbox/eigenaaridentiteit. Hij vindt
onder de mailboxlease plaats vóór de Graph-preflight en opnieuw in de callback
vlak vóór PATCH/move. De actuele leasetoken en monotone deadline worden eveneens
opnieuw gecontroleerd. Onleesbare benodigde historie blokkeert de operatie.
Dit hangt niet af van de Send-releasevlag en blijft gelden na reconnect.

De route-inventaris omvat daarnaast Compose create/PATCH, tekst- en HTML-metadata
Drafts-editors, bijlagen toevoegen/verwijderen, Discard, de nieuwe Send-route en
de oudere outbound-route. De bestaande conceptguards blijven behouden; create
wijzigt geen bestaand bericht en de legacy-sendroute blijft afzonderlijk gesloten.
Geen algemene of willekeurige Graph-mutatie-endpoint toegevoegd.

### 2. Expliciet bodycontract

Bron: [Graph v1.0 Get message](https://learn.microsoft.com/en-us/graph/api/message-get?view=graph-rest-1.0).
De algemene Graph-reader en beveiligde HTML/CID-weergave zijn ongewijzigd.
De nieuwe, afzonderlijke `microsoft_compose_body_contract.py` wordt alleen door
Compose en bestaande-concept-Send gebruikt.

1. **Opgeslagen concept:** de normale Graph-respons blijft de oorspronkelijke
   defaultrepresentatie (doorgaans HTML). De volledige body inclusief type wordt
   exact in snapshot/fingerprint opgenomen. Voor de editor wordt afzonderlijk
   `Prefer: outlook.body-content-type="text"` gevraagd. Eén ondubbelzinnige
   `Preference-Applied`-bevestiging, teksttype en overeenkomstige versie/metadata
   zijn vereist. De tekstprojectie bewijst zelf niets over oorspronkelijke HTML.
2. **Onafhankelijke bodybron:** GET op exact hetzelfde immutable bericht-ID met
   `/$value` levert MIME. Alleen een expliciete `text/plain`-berichtbody wordt
   geaccepteerd, zelfstandig of als eerste deel van een vlak `multipart/mixed`
   met uitsluitend attachment-delen daarna. Geen HTML-body, alternative/related,
   geneste multipart, inline bodyalternatieven of onbekende charsetfallback.
   Voorlopig ASCII/UTF-8, begrensde overdrachtscoderingen, maximaal 16 MiB MIME,
   maximaal 12 delen en 512 KiB gedecodeerde berichtbody; Compose's bestaande
   kleinere tekstlimiet blijft gelden. Parserwerk wordt vóór parsing begrensd.
   MIME-tekst en bevestigde Graph-projectie moeten overeenkomen. Alleen CRLF/LF
   worden als dezelfde regelovergang behandeld; geen trimming, Unicode- of
   HTML-normalisatie. De HTML-vingerafdruk blijft volledig exact.
3. **Goedgekeurd manifest:** behoudt alle bestaande veld- en bijlagehashes,
   inclusief volledige default-Graph-body, en voegt `nativeTextHash` toe.
   MIME/berichttekst/bijlagebytes worden niet in ledger of logs opgeslagen.
4. **Exacte Sent-kopie:** opnieuw onafhankelijke defaultbody-, tekstpreferentie-,
   MIME- en bijlageverificatie op dezelfde immutable ID. Een verschil in volledige
   HTML is een afwijking, ook bij dezelfde zichtbare tekst. Provider-normalisatie
   wordt niet stilzwijgend gelijkgesteld. Ontbrekend native-bodybewijs bij verder
   gelijke velden blijft onzeker; een betrouwbaar geobserveerde veldafwijking
   blijft een afwijking. Geen automatische retry of ontgrendeling.

`/$value` wordt behandeld als Microsofts MIME-representatie van het bericht,
geen cryptografisch bewijs van de ontvangen wire-MIME of aflevering.
Legitieme providerrepresentaties buiten dit beperkte contract blijven veilig
geblokkeerd. Bestaande pending manifests zonder `nativeTextHash` blijven leesbaar
maar kunnen niet achteraf van dat ontbrekende goedkeuringsbewijs worden voorzien.
Eerder bevestigde historische resultaten blijven behouden.

Er zijn expliciete publieke meldingen voor een niet-verifieerbare body en voor
het geval dat Microsoft mogelijk al een concept heeft opgeslagen terwijl de
bodycontrole daarna faalt. In dat laatste geval blijft de Compose-claim onzeker;
herladen of hetzelfde verzoek herhalen maakt geen nieuw concept.

### Verificatie

- Microsoft-backend: **787/787**, inclusief 18 nieuwe Fixes 4-tests; fake Graph,
  geïsoleerde CAS, lege credentialomgeving, geen socket/DNS/child-processpogingen.
- Werkelijk afzonderlijke serverprocessen met tijdelijke SQLite/fake Graph:
  **2/2**. Migratie en maximaal één send blijven behouden.
- Gerichte Send-client: **32/32**; Compose-client: **21/21**.
- Bredere Microsoft-clientrun: **46/48 suites**. De twee fouten zijn dezelfde als
  de voorafgaande Fixes 3-baseline: `MicrosoftFilteredSenderAction.test.ts`
  (verwacht `<MicrosoftFilteredSenderAction`) en `MicrosoftMailboxFolderRail.test.ts`
  (verwacht `lg:flex`). Deze bestaande, niet-gerelateerde suites zijn niet aangepast.
- Auth/OAuth: **185/185**, vijf afzonderlijke suiteprocessen, netwerkguard.
- TypeScript app/node: geslaagd. Volledige frontend-build: geslaagd; bestaande
  waarschuwing voor bundles groter dan 500 kB, geen buildfout.
- Gmail/IMAP-bronbeveiliging: **645 beschermde bestanden ongewijzigd**.
  Gedeelde API buiten Microsoft behouden. `git diff --check` geslaagd.

Gerichte scenario's omvatten externe Inbox-verplaatsing van een onzekere of
inhoudelijk afwijkende verzending, alle vier Inbox-acties, shutdown/reconnect,
ontbrekende opslag, verkeerde mailboxidentiteit, een nieuwe blokkade tijdens
preflight, leaseverlies, gewone Inbox-berichten, default HTML, ontbrekende of
conflicterende preferences, HTML met dezelfde tekst, Draft/Sent-normalisatie,
MIME-grenzen, historische manifests en herladen zonder automatisch opnieuw zenden.

Bewijs: `/private/tmp/cuevion-fixes4-backend-final.log`,
`/private/tmp/cuevion-fixes4-processes.log`, `/private/tmp/cuevion-fixes4-client/`,
`/private/tmp/cuevion-fixes4-focused-final/`, `/private/tmp/cuevion-fixes4-auth/`
en `/private/tmp/cuevion-fixes4-build-final.log`.

### Niet live bewezen en vrijgavevoorwaarden

Beide concrete implementatiebevindingen zijn lokaal gecorrigeerd. Nog niet live
bewezen zijn Microsofts precieze MIME-vorm voor deze mailbox, bevestigingsheaders,
regelovergangen, eventuele alternative/HTML-omzetting, metadata/body-normalisatie
tussen Draft en Sent en latency/kosten van de extra reads. De verifier maakt
geen aannames om ontbrekend bewijs te omzeilen; dergelijke uitkomsten blokkeren.
De extra MIME-lezingen vergroten wel de latency/egress en moeten operationeel
worden gemeten. Er is geen atomische Outlook-controle-plus-send ontstaan:
Sent-verificatie detecteert achteraf en voorkomt het externe racevenster niet.

Een eigen-accounttest vereist afzonderlijke toestemming, een afgeschermde
niet-Production testomgeving met exact eigenaar/accountbeleid, gecontroleerde
Mail.Send-/release-instelling in een latere geautoriseerde stap, onschuldige
inhoud/eigen ontvanger, duurzame opslag met capaciteit en een stopprotocol voor
onzekerheid/afwijkingen. Geen andere schrijvende client tijdens de test. Deze ronde
activeert niets en vormt geen Production-vrijgave. Opslaggroei, quota, latency,
herstel zonder bewijsverlies en expliciete acceptatie van het externe racevenster
blijven voorwaarden voor Production.

Geen echte Graph-mutaties of e-mails, Mail.Send-activering, releasevlagwijzigingen,
Gmail/IMAP- of Reply/Forward-wijzigingen, commit, push of deployment.
