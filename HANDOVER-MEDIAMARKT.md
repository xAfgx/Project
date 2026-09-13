# ARES Handover – Pokémon Center (fertig) + MediaMarkt (geplant)

Stand: 13.09.2026. Workspace: `C:\Users\A\Desktop\aresUpdatedares`

## 1. Git-Save-Points (Rückwege)

```
53af228  smooth Scroll + Klick-Variation + kürzere Pausen   ← AKTUELL
6a9e54e  stabiler Checkout, instant Scroll, Country-Fix
e074b65  UI-Baseline (i18n, Modul-Hub)
```

- Zurück: `git reset --hard <commit>` danach `npm run build:backend`
- Nur eine Datei: `git checkout <commit> -- <datei>`
- Nicht-committete Änderungen retten: `git stash` / `git stash pop`

## 2. Pokémon-Center-Runtime (im Commit 53af228, läuft)

- `src/commerce/pokemon-center/release-journey.ts`
  - `addToCart`: Klick → auf natürliche Cart-Navigation warten, `goto` nur Fallback
  - `openCheckout`: Klick → auf Checkout warten (Titel/Marker), `goto` nur Fallback (10s)
  - `waitForAddToCart`: 12s (Produktseite)
  - loggt `[JOURNEY] discover click-card href=…`
- `src/browser-worker/early-gate-task-executor.ts`
  - Queue→Startseite nur einmal (kein Reload), `waitForStorefront`
- `src/browser-worker/semantic-field-autofill.ts`
  - **Country-Fix**: Select-Optionen einmal lesen, Ist-Auswahl über Value/Text/`Intl.DisplayNames` erkennen → `DE`→Value 69, spart ~5s
- `src/browser-worker/interaction-models.ts`: Klick-Pausen −35 % (pre 117–338, post 143–416 ms)
- `src/browser-worker/interaction-engine.ts`: Cursor-Position persistent (`page["aresPointer"]`), Start an plausiblem Punkt statt (0,0)
- `src/browser-worker/ui-interaction-helper.ts`: eindeutiger Klick-Seed pro Klick (`…:click:<Zähler>`) → variierende Klickpunkte
- `src/browser-worker/client.ts`: `[JOURNEY]`-Logs werden nach außen geleitet
- `python/seleniumbase_cdp/task_browser_worker_oopif_impl.py`
  - **Smooth Scroll**: `scroll-into-view` → `_smooth_scroll_into_view` (natives CDP-Mausrad, KEIN JS-Inject)
  - Position aus derselben Quelle wie der Klick: `getBoundingClientRect` im Frame + `_frame_viewport_offset`
  - Fallback: `DOM.scrollIntoViewIfNeeded`

## 3. Pokémon-Center Offline-Harness

Ordner: `C:\Users\A\Downloads\SEITEN\pokemoncenter_offline_flow`
Server: `server.py` (Ports **18080** + **8080**), starten:
```
$env:ARES_OFFLINE_PORT="18080"; python server.py
```
- `server.py`: injiziert Navigations-Skript (Buttons „In den Einkaufswagen"/Gast-Checkout) + Karten-Gate ins srcdoc + Request-Log
- `queue 1.html`: Queue (30s) → `/de-de`
- `demo rätsel 2.html` (Captcha-Gate), `captcha_demo.html`
- Captcha aktuell NICHT im Flow (Sitekey defekt/offline)

Probe: `tools/ares-runtime-probe.js` (Monitor→Child-Lane, Payment-Session mit Testkarte, `payment-ready`)
Start:
```
$env:ARES_OFFLINE_BASE_URL="http://127.0.0.1:18080/queue.html"
$env:ARES_PROBE_RUNS="1"; $env:ARES_PROBE_HEADLESS="0"
node tools/ares-runtime-probe.js
```

## 4. MediaMarkt Offline-Harness (FERTIG, verifiziert)

Ordner: `C:\Users\A\Downloads\markt` — Server `server.py`, Port **18090**:
```
$env:ARES_MARKT_PORT="18090"; python server.py
```
Routen: `/`, `/search/airtag`, `/search/pokemon`, `/de/product/…`,
`/de/cart`, `/de/checkout/overview`, `/de/checkout/shipping`, `/de/checkout/payment`, `/de/login`

Verdrahtung (in `server.py` injiziert):
- Suche: Enter/Submit → `/search/airtag` bzw. `/search/pokemon`
- „In den Warenkorb" → `/de/cart`
- `[data-test=checkout-continue-button]`: Cart→Overview→Shipping→Payment
- **Alle externen Links/Form-Actions neutralisiert** (mediamarkt.de → lokal, Rest → `#`)

Wichtige Selektoren (aus den echten Seiten):
- Produkt/Suche: Button-Text „In den Warenkorb"
- Checkout: `[data-test=checkout-continue-button]`
- Payment: `#cardNumber`, `#expMonth`, `#checkNumber`, `#cardholderName`

Gespeicherte Seiten (SingleFile): Startseite, `Suchergebnis "airtag"`, `POKÉMON Neuheiten`,
`POKÉMON Sammelkartenspiel`, Produkt `APPLE AirTag`, Produkt `POKEMON 45944 …`,
`Warenkorb`, `Login`, `Übersicht`, `Versand`, `Payment Page`.

Verifizierter Flow (Test):
`/ → /search/airtag → Produkt → /de/cart → overview → shipping → payment`, Kartenfelder vorhanden.

## 5. MediaMarkt-Runtime-Modul (NOCH ZU BAUEN)

Ziel: **eigenes Modul, direct** – ein Browser, kein Monitor-Spawn, keine Queue.

1. `src/commerce/mediamarkt/release-journey.ts` (implementiert `ReleaseJourney`)
   - `supports`: hostname mediamarkt.de / localhost / 127.0.0.1
   - `discover`: Shop öffnen → Suchfeld tippen + absenden → Ergebnisliste scannen → Produkt klicken
   - `addToCart`: „In den Warenkorb" klicken → auf Warenkorb warten
   - `openCheckout`: `checkout-continue-button` bis Payment (Zustand abwarten)
   - `isReadyForFinalSubmit` / `submitOrder` (Guard) / `isOrderConfirmed`
2. `src/browser-worker/mediamarkt-task-executor.ts` (**direct**)
   - analog `EarlyGateBrowserTaskExecutor`, aber OHNE Queue/Monitor
   - Context erstellen → Journey → `SemanticCheckoutPreparer` → Final-Purchase-Guard (aus)
3. Registrierung in `src/browser-worker/worker.ts`
   - neuer Branch: `platform === "mediamarkt"` → MediaMarkt-Executor

Schon erledigt:
- `src/commerce/platforms.ts`: Plattform `mediamarkt` + Capability
- `src/app/app.component.ts`: UI-Modul „MediaMarkt" (Akzent `#e2001a`, Modus „Direkt zum Checkout")
- `src/commerce/mediamarkt/release-journey.ts`: **fertig, kompiliert** (`npm run build:backend` = 0)
  - `discover` (Suche → bester Treffer → Klick → Add-to-Cart warten)
  - `addToCart`, `openCheckout` (Wizard-Schritte mit Zustandswarten), `advanceCheckout`,
    `isReadyForFinalSubmit`, `submitOrder` (Guard), `isOrderConfirmed`
  - nutzt `SearchHit`-Scan über `a[href*="/de/product/"]` + `ProductMatcher`
  - Harness-Erwartung: Such-URL `/de/search.html?query=<q>` (im MediaMarkt-`server.py` noch als Route ergänzen,
    die je nach Query auf `/search/airtag` bzw. `/search/pokemon` leitet)

Noch offen:
1. `src/browser-worker/mediamarkt-task-executor.ts` (direct, ohne Monitor/Queue)
2. Registrierung in `src/browser-worker/worker.ts` (`platform === "mediamarkt"` → direct)
3. Harness-Route `/de/search.html?query=…` ergänzen
4. UI-Build prüfen (`ng build`)

## 6. Harte Anforderungen an das Modul

- **Generisch stabil**, nicht nur offline.
- **State-Änderungen wegstecken**: Cookie-Consent, Popups, zusätzliche Checkout-Zwischenschritte.
- Immer auf echten Zustand warten, nie blind klicken.
- Kein JS-Inject (nur native CDP-Events / Runtime.evaluate wie überall).
- Finaler Kauf bleibt aus (Guard).
- Bestehenden Pokémon-Pfad NICHT anfassen.

## 7. Nützliche Befehle

```
npm run build:backend        # nach jeder src-Änderung
git log --oneline            # Save-Points
python -m py_compile <datei> # Python-Syntaxcheck
```

## 8. Bekannte Eigenheiten

- Offline-Seiten sind durch SingleFile aufgebläht (7 MB) → Scans langsamer als live.
- ARES `page.url()` ist gecacht (stale) → live-Titel/DOM nutzen, nicht die URL.
- Klicks sind pro Element deterministisch im Task (Seed = Task-ID); Variation jetzt pro Klick.
- MediaMarkt-Checkout-Seiten haben 0 Scripts → Buttons brauchen die Harness-Verdrahtung.
- Cookie-Snapshot-Modul existiert für eingeloggte Sessions (nur eigener Account, manuell).
