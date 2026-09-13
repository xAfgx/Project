# MediaMarkt Live Handover

Stand: 2026-09-13, Europe/Berlin

## Kurzstatus

Der MediaMarkt-Flow ist nicht als "final fertig" zu betrachten. Der letzte Live-Lauf wurde auf Wunsch des Users gestoppt, weil der Cookie-Banner noch sichtbar war und der Bot trotzdem weiter navigieren wollte.

Danach wurde nur MediaMarkt-spezifisch nachgezogen:

- Cookie/Consent ist jetzt ein harter Gate vor der Suche.
- Wenn der Cookie-Banner sichtbar bleibt, startet der Bot keine Suche und keine weitere Navigation.
- Ein Cookie-Klick gilt nur noch als erfolgreich, wenn der native Locator-Klick wirklich resolved.
- Falls die normalen MediaMarkt/OneTrust/Usercentrics-Locators nicht reichen, wird die bestehende `dismissConsentPopups(true)`-RPC-Funktion nur als Fallback genutzt.
- Der Backend-Build wurde danach erfolgreich ausgefuehrt: `.\node_modules\.bin\tsc.cmd -p tsconfig.backend.json`.

Nicht live bestaetigt nach dieser letzten Cookie-Gate-Aenderung, weil der User explizit stoppen wollte.

## Harte User-Vorgaben

- Kein echter Kauf.
- Live-Test nur bis finaler Checkout-/Review-Zustand, niemals final submit.
- Keine neuen JavaScript-Injections.
- Nichts in `src/commerce/pokemon-center/release-journey.ts` schreiben.
- MediaMarkt muss mit existierender Bezier/GhostMouse-/native-Locator-Runtime laufen.
- Keine Screenshot-/SigLIP2-Abhaengigkeit fuer den MediaMarkt-Suchflow.
- Keine direkte Checkout-URL-Magie.
- MediaMarkt-Adresse muss zuerst ueber `Adresse suchen...` laufen; Einzeladressfelder sind kein primaerer Fallback.
- Shop-spezifische Fixes duerfen andere Module nicht blockieren oder synchronisieren.

## Was bereits funktioniert hat

Ein echter Live-Lauf kam stabil bis in den Warenkorb/Checkout-Einstieg:

- Start auf `https://www.mediamarkt.de`.
- Suchfeld gefunden.
- `AirTag` nativ getippt.
- Suche mit Enter bestaetigt.
- Such-URL erkannt.
- Echter Produktlink aus der Ergebnisliste angeklickt.
- Produktseite bestaetigt.
- Add-to-Cart genau einmal geklickt.
- Mini-Warenkorb erkannt.
- `Zum Warenkorb` im Popup ueber eindeutigen `data-test` geklickt.
- `Zur Kasse gehen` im Warenkorb gefunden.
- `Weiter als Gast` korrekt erkannt und geklickt.

Referenzlauf:

`runtime-probe-logs/markt-2026-09-13T14-38-09-049Z.summary.json`

Dieser Referenzlauf war nur bis `checkout-opened` erfolgreich. Er ist kein voller Checkout-Erfolg.

## Offene Stelle

Der volle Lauf bis finaler Review-/Payment-ready-Zustand ist noch nicht bestaetigt.

Der aktuell wichtigste offene Live-Test ist:

1. Frisches MediaMarkt-Profil starten.
2. Cookie-Banner muss sichtbar erkannt werden.
3. Cookie-Banner muss geschlossen werden.
4. Erst danach darf Sucheingabe passieren.
5. Nach `Weiter als Gast` muss zuerst `Adresse suchen...` befuellt werden.
6. Danach darf Profil-/Payment-Preparation weiterlaufen.
7. Stop bei Review-/Payment-ready, kein final submit.

## Root Causes

### Newsletter-Fokus

MediaMarkt laedt nach der Suche Vorschlaege und scrollt zeitweise selbst Richtung Footer/Newsletter. Alte generische Vorschlags- oder Submit-Klicks konnten dann Newsletter-/Marketingflaechen treffen.

Fix:

- Keine Suggestion-Klicks.
- Kein generischer Submit-Klick.
- Kein direkter Search-URL-Fallback.
- Suche nur durch natives Tippen und Enter.
- Marketing-/Newsletter-Selektoren sind ausgeschlossen.

### Stale URL

`page.url()` war im Node/RPC-Transport teilweise stale. Die echte Browser-URL war schon weiter, aber ARES sah noch die alte Startseite und startete falsche Retry-Pfade.

Fix:

- `refreshCurrentUrl()` / `current-url` RPC wurde eingebaut.
- MediaMarkt nutzt fuer Zustandswechsel die frisch abgefragte URL.
- Kein fester Worker-Sleep nach Enter.

### Add-to-Cart Doppelclick

Ein Add-to-Cart-Klick konnte im Worker timeouten, obwohl der Klick live bereits ankam und das Mini-Basket-Popup offen war. Der alte Retry traf danach Popup-Empfehlungen und fuehrte wieder auf Produktseiten.

Fix:

- Add-to-Cart wird exakt einmal dispatcht.
- Danach zaehlen nur Zustandsmarker: Mini-Basket, Warenkorbmarker, Counter oder Warenkorb-/Checkout-URL.
- Kein zweiter Klick auf alte PDP-Koordinaten.

### Telefonfeld wurde mehrfach gefuellt

MediaMarkt nutzt `type=tel`; der generische Semantic Resolver hat optionale numerische Felder zu oft als Telefon interpretiert. Durch wiederholte Preparation-Schleifen wurde die Telefonnummer mehrfach angehaengt.

Fix:

- Nur MediaMarkt-Executor: `guestProfile.contact.phone = ""`, weil Telefon optional ist.
- Profil-Preparation laeuft nur bis `profileReady`, nicht endlos weiter.
- Payment-CVV-Selector wurde entschaerft, damit keine Zahlungsdaten in falsche Felder geraten.

### Adresse wurde falsch herum befuellt

MediaMarkt erwartet zuerst das Autocomplete-Feld `Adresse suchen...`; manuelles Befuellen von PLZ/Stadt/Strasse/Hausnummer kann Validierungsfehler erzeugen.

Fix:

- Nur MediaMarkt-Executor hat `fillAddressSearch(...)`.
- Es tippt `Musterstrasse 12, 10115, Berlin`.
- Danach `ArrowDown` + `Enter`.
- Erst danach darf die generische Profilvorbereitung weiterlaufen.
- Wenn `Adresse suchen...` nicht erkannt wird, wird abgebrochen statt falsche Einzeladressfelder weiter zu befuellen.

## Letzte Code-Aenderung

Datei:

`src/commerce/mediamarkt/release-journey.ts`

Neue Logik:

- `ensureConsentCleared(page, timeoutMs, stage)`
- `hasVisibleConsent(page)`
- `dismissConsent(page)` zaehlt nur echte resolved Klicks als Erfolg.
- Vor Suchfeld-Nutzung und vor Search-Submit wird Consent erneut hart geprueft.
- Bei sichtbarem Cookie-Banner: Fehler statt Navigation.

Wichtig: Diese letzte Cookie-Gate-Aenderung ist gebaut, aber noch nicht live gegen MediaMarkt bestaetigt.

## Isolation

- `src/commerce/pokemon-center/release-journey.ts` wurde in dieser Runde nicht bearbeitet.
- MediaMarkt-spezifische Checkout-Autocomplete-Logik liegt in `src/browser-worker/mediamarkt-task-executor.ts`.
- MediaMarkt-Journey liegt in `src/commerce/mediamarkt/release-journey.ts`.
- Fast-Path im Python-Worker ist an Task-IDs mit Prefix `direct_markt` gebunden.
- Es gibt keine globale Synchronisierung, keinen globalen Lock, keinen gemeinsamen Blocker fuer andere Shops.

## Naechster sinnvoller Schritt

Einen einzigen Live-Lauf starten und nur auf diese Checks schauen:

- Wird Cookie vor jeder Navigation/Suche wirklich geschlossen?
- Bleibt der Bot stehen, falls der Banner sichtbar bleibt?
- Wird `Adresse suchen...` erkannt und mit Autocomplete bestaetigt?
- Wird die Hausnummer danach automatisch/korrekt gesetzt?
- Kommt der Flow bis Payment-/Review-ready ohne finalen Kauf?

Wenn der Cookie-Banner nach dem Gate noch sichtbar bleibt, muss als naechstes der konkrete Button/Container aus dem Live-Debug in `CONSENT_VISIBLE_SELECTOR` oder `CONSENT_BUTTON_SELECTOR` ergaenzt werden, statt wieder generische Klicks zu bauen.

## Verifikation

- Backend-TypeScript-Build nach letzter Aenderung: erfolgreich.
- Kein Live-Test nach letzter Cookie-Gate-Aenderung, weil der User `STOPP` gesagt hat.
- Kein finaler Kauf wurde ausgeloest.
