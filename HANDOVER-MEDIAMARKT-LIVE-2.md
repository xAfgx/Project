# HANDOVER 2 – MediaMarkt Live-Stand (13.09.2026, ~15:45)

Hallo, hier ist der zweite Live-Stand zum MediaMarkt-Modul, direkt als Fortsetzung von `HANDOVER-MEDIAMARKT-LIVE.md`. Der große Blocker aus Handover 1 ist gelöst: **Add-to-Cart trifft jetzt live den richtigen Button** (sichtbarer Sticky-Button, Center-Klick, `cart-confirmed` über Mini-Basket – mehrfach live bewiesen), und der Flow kommt bis **`/de/checkout`**. Offen ist im Kern nur noch der Wizard-Schritt **„Zur Kasse gehen"** (Fix liegt im Quellcode, ist aber noch nicht live verifiziert), dazu der kosmetische **Newsletter-Scroll** (kommt von MediaMarkt selbst) und die **Such-Latenz** durch das Vorschlags-Popup. Ganz wichtig: **zuerst `npm run build:backend` und ARES komplett neu starten** – der letzte Build wurde abgebrochen, `dist/` und Quellcode sind bei der Suche nicht synchron. Der neue Fast-Mode ist strikt auf `direct_`-Tasks begrenzt, **Pokémon bleibt garantiert unverändert**.

Übergabe des aktuellen Arbeitsstands. Der vorherige `HANDOVER-MEDIAMARKT-LIVE.md` bleibt gültig als Kontext; dieses Dokument beschreibt **was seitdem passiert ist, was live bewiesen ist und was offen ist**.

Workspace: `C:\Users\A\Desktop\aresUpdatedares`

## 0. WICHTIG vor dem nächsten Start

- **Letzter Build wurde abgebrochen.** Der Quellcode hat den restaurierten Such-Flow (Tippen + Vorschlag), `dist/backend` hat noch eine ältere Variante (Suche per direktem URL-Goto). Deshalb zuerst:
  ```
  npm run build:backend
  ```
- Danach **ARES komplett beenden und neu starten** (sonst läuft der alte Browser-Worker-Child weiter).
- Python-Änderungen brauchen keinen Build (Worker wird pro Task frisch gespawnt).
- Live-Task: Modul MediaMarkt → Modus **„Direkt zum Checkout"** → Task-ID muss mit `direct_` beginnen.

## 1. Live bewiesen (mit Zeitstempeln aus Debug/Stderr)

- **Consent**: Banner wird per `[data-test="pwa-consent-layer-accept-all"]` weggeklickt.
- **Suche** (Lauf 14:57): `airtag` tippen → Vorschlag `#popup-list-item-0` klicken → `/de/search.html?query=airtag…suggest…` (3s nach dem Tippen).
- **Produktseite**: erreicht (14:57, 15:19).
- **Add-to-Cart** (Läufe 14:58, 15:19): Ranking wählt den sichtbaren Sticky-Button (`x=980,y=677`, in View), Klick auf die Mitte per `page.mouse.click` → **`cart-confirmed signal=mini-basket`** nach ~1–5s. Vorher verpuffte der Klick immer.
- **Mini-Basket → Checkout** (Lauf 15:19): `minibasket-primary` („Zum Warenkorb", `href=/de/checkout`) geklickt → **`/de/checkout` erreicht**.
- **Fast-Mode** (Läufe 15:19/15:28): RPCs 2–3s statt 5–45s. Keine 45s-Hänger mehr.

## 2. OFFEN (genau hier weitermachen)

1. **Wizard-Schritt „Zur Kasse gehen"** (Lauf 15:19): Button wurde gefunden, hatte aber `x=0,y=0,w=0,h=0` → Klick ins Leere, kein URL-Wechsel, danach Timeouts. **Fix liegt im Quellcode, noch nicht live verifiziert**:
   - `CONTINUE_TESTID` um `checkout-continue-desktop-enabled` / `-mobile-enabled` / `… button` erweitert.
   - Neues `pickClickable()` wählt die sichtbare Variante (Box/covered/enabled, In-View bevorzugt) statt `.first()`.
   - Wizard klickt danach `bringIntoView` + `clickControlCenter` (Center-Klick), Locator-Klick nur als Fallback.
2. **Newsletter-Scroll nach dem Tippen** (offener Punkt #2 aus Handover 1, Ursache weiterhin Site-JS):
   - Ablauf: nach dem Tippen scrollt MediaMarkt selbst zum Footer-Newsletter (`scrollY=5570`), der Vorschlag liegt dann bei `y=-5478`.
   - Der Maus-Pointer bleibt nach dem letzten Klick (Consent) an dessen Viewport-Position stehen; wenn die Seite scrollt, liegt „Jetzt anmelden" darunter → sieht aus wie ein Newsletter-Hover/-Klick. **Es wird nichts am Newsletter geklickt** (`EXCLUDED_MARKETING_SELECTOR`).
   - Nicht ohne JS-Inject verhinderbar. Möglich ist nur: nach dem Tippen zurück nach oben scrollen + Pointer neutral parken (war testweise drin, im aktuellen Quellcode **nicht** enthalten).
3. **Suche ist live langsam**: Sobald das Vorschlags-Dropdown offen ist, laufen `rpc:count`/`rpc:press` in 5s-Timeouts (Läufe 15:28, 15:35). Enter allein submitted live nicht zuverlässig; der Vorschlags-Klick funktioniert, kostet aber ~20s durch die vorherigen Timeout-Versuche.
   - Getestete, vom Nutzer **abgelehnte** Abkürzung: direktes `goto` auf die Such-URL (funktioniert, umgeht Popup+Scroll). Aktuell NICHT im Quellcode.
4. **Parallele Tasks**: Architektur kann bis zu 4 Tasks gleichzeitig (Worker-Pool, `ARES_MAX_CONCURRENT_TASKS`, Default 4). Bedingung: **jedes Task ein eigenes Profil** (Chrome-Profil-Lock, `profile-session-manager.ts`, `.ares-profile.lock`). Gleiches Profil = exklusiv.

## 3. Änderungen in diesem Abschnitt

### MediaMarkt-only: `src/commerce/mediamarkt/release-journey.ts`
- **Add-to-Cart** (Kern-Fix, live bewiesen):
  - `findAddToCartButton` → `rankAddToCartControls`: alle sichtbaren Kandidaten (`#pdp-add-to-cart-button`, `button[data-test*="cofr-add-to-basket" i]`), 1 Evaluate je Kandidat (Box, visible, enabled, marketing, covered, position), Score = In-View (4) + nicht überdeckt (2) + sticky (1). Live: Sticky-Button gewinnt.
  - `clickControlCenter`: prüft In-View + `isCovered`, klickt dann `page.mouse.click(Mitte)`. **Kein Klick auf überdeckte/off-viewport Controls** → keine Empfehlungsklicks mehr.
  - `readCartCount`: generischer Header-Zähler (Cart/Basket-Selektoren, nur oberer Bereich).
  - `waitForCart(page, timeout, baseline)`: URL / Cart-Marker / Mini-Basket / Zähler > Baseline, loggt `cart-confirmed`.
  - Retry: max. 1×, nur wenn nachweislich nichts passierte; Retry ebenfalls nur Center-Klick.
- **Checkout**:
  - `openCheckout`: Modal-Primary-Klick (Handover-Verhalten) + Wizard-Loop mit `pickClickable`/`bringIntoView`/`clickControlCenter`.
  - `findButton` nutzt `pickClickable` (0x0-Button-Fix).
  - `CONTINUE_TESTID` erweitert (desktop/mobile/Child).
- **Discover**: Produkt-Navigation jetzt `page.goto(hit.url)` statt Anchor-Klick (live war der Klick zu langsam → Fallback).
- **Timeouts** an Live-Latenz: `hasMiniBasket` 1500, `firstVisible` 1500, `onProductPage` 1000, `hasCartMarker` 1500, `hasPaymentFields` 2500, Wizard-Clicks 8000, Polls 500ms.
- **Suche**: wieder Handover-Flow (Tippen → Vorschlag → Enter → Submit-Button). Kein URL-Goto.
- Ungenutzt: `settleAfterNavigation` (Helper) und `searchFromStartPage`-Reste können nach Validierung entfernt werden.

### Shared, aber strikt gated: `python/seleniumbase_cdp/task_browser_worker.py`
- Neuer **Fast-Mode** (`TaskRpcRuntime.fast_mode`, Default `False`):
  - `run()` setzt `fast_mode = taskId.startswith("direct_")` → **nur MediaMarkt-Direct-Tasks**.
  - `rpc()` überspringt im Fast-Mode `_ensure_session_alive()` und `_sync_newest_target()`.
  - `_fast_url()` cached `window.location.href` 1,5s (spart einen CDP-Evaluate pro RPC); außerhalb des Fast-Modes identisch zum alten Verhalten.
  - Main-Loop ruft `adapter.poll_runtime()` im Fast-Mode nicht auf.
  - Alle 14 `str(self.sb.get_current_url() or "")` → `self._fast_url()`; für `gate_`/`auto_`/`monitor_` (Pokémon) ist das Verhalten **byte-identisch**.
- **Garantie**: Pokémon-Tasks haben `fast_mode=False` und damit unverändertes Verhalten.

### Tooling
- `tools/ares-markt-probe.js`: Task-ID auf `direct_markt-probe-…` geändert, damit der Fast-Mode im Probe greift.
- Live-Probe gegen `https://www.mediamarkt.de` ist möglich:
  ```
  $env:ARES_MARKT_BASE_URL="https://www.mediamarkt.de"; $env:ARES_PROBE_HEADLESS="0"; node tools/ares-markt-probe.js
  ```

### Temporäre Debug-Reste (nach Live-Validierung entfernen)
- `src/browser-worker/seleniumbase-rpc-page.ts`: `TEMPORARY LIVE DEBUG` Slow-RPC-Logging (Zeilen ~62–87). Verhaltensneutral.
- `release-journey.ts`: `mediamarktDebug`-Ringpuffer + Debug-Einträge (war schon vorher temporär), ungenutzter `settleAfterNavigation`.
- `client.ts`: wieder original (nur `[MONITOR]`/`[JOURNEY]`-Forwarding).

## 4. Live-Debug-Referenz (Lauf 15:19, der weiteste)

```
13:19:26 page-top / search-field
13:19:29 search-typed
13:19:40 search-suggestion  y=-5478 scrollY=5570   ← Newsletter-Scroll der Seite
13:19:43 search-submitted
13:19:47 product-click
13:19:50 add-to-cart-candidates: sticky x=980,y=677 score 6
13:19:51 add-to-cart-mouse x=1084,y=701
13:19:56 cart-confirmed signal=mini-basket          ← Add-to-Cart live bestätigt
13:19:56 minibasket-primary "Zum Warenkorb" href=/de/checkout
13:19:59 checkout-step step=0 before=/de/checkout   ← /de/checkout erreicht
         button "Zur Kasse gehen" w=0,h=0           ← DAS war der Blocker
```
Debug-Datei: `%TEMP%\ares-mediamarkt-debug.log` (Ringpuffer, 2s-Flush) + `mediamarktDebug` in `%APPDATA%\ares-task-orchestrator\ares.sqlite`.
Stderr (mit Redirect gestartet): `%TEMP%\ares-stderr.log`.
Auslesen: `python "C:\Users\A\AppData\Local\Temp\opencode\dump-ares-debug.py"`.

## 5. Nächste Schritte (Empfehlung)

1. `npm run build:backend`, ARES neu starten, **ein** Live-Direct-Run. Erwartung: Popup → `/de/checkout` → Wizard klickt „Zur Kasse gehen" (sichtbare Variante) → Shipping/Payment.
2. Falls Wizard weiter klemmt: `checkout-step`-Debug ansehen (welche Box/URL), ggf. `checkout-continue-*-enabled`-Varianten im echten HTML prüfen.
3. Newsletter-Scroll: entscheiden, ob nach dem Tippen zurückgescrollt + Pointer geparkt werden soll (kosmetisch, Site-JS bleibt).
4. Such-Latenz: entweder Vorschlags-Klick mit längeren Timeouts akzeptieren oder direkte Such-URL (funktioniert, war aber nicht gewünscht).
5. Nach erfolgreicher Live-Validierung: Debug-Reste entfernen (Abschnitt 3), Build, committen.

## 6. Wichtige Erkenntnisse

- Der Add-to-Cart-Klick verpuffte, weil `#pdp-add-to-cart-button` der Button **unter dem Fold** ist; der sichtbare Sticky-Button hat **keine** PDP-ID. Ranking nach In-View/covered löst das generisch für alle Produkte.
- `isVisible` ist CSS-only; Klickbarkeit braucht Box + `elementFromPoint`.
- Live-Latenz kommt aus der geteilten Worker-Logik (Session-Recovery + Watchdog pro RPC) und der schweren Live-Seite; Fast-Mode für `direct_` reduziert das auf 2–3s/RPC.
- Das Vorschlags-Dropdown blockiert den Seiten-Hauptthread (5s+ pro Evaluate); Enter submitted live nicht zuverlässig, der Vorschlags-Klick schon.

## 7. PFLICHT am Ende: Debug komplett raus (erst NACH erfolgreicher Live-Validierung!)

Die gesamte temporäre Debug-Scheiße muss entfernt werden, sonst bleibt sie für immer drin. Konkret:

1. `src/browser-worker/seleniumbase-rpc-page.ts`
   - `TEMPORARY LIVE DEBUG` Slow-RPC-Tracing entfernen: `Pending`-Typ wieder ohne `label`/`startedAt`, `requestInternal` ohne Label/Timeout-Log, `consume` ohne `rpc-slow`-Log. (Zeilen ~9, ~62–68, ~85–87)
2. `src/commerce/mediamarkt/release-journey.ts`
   - `mediamarktDebug`-Ringpuffer + `debug()`-Methode und **alle** `this.debug(page, …)`-Aufrufe entfernen.
   - `describe()` entfernen, sobald keine Debug-Aufrufe mehr darauf zugreifen.
   - Ungenutzten `settleAfterNavigation`-Helper entfernen.
   - Kommentare `TEMPORARY LIVE DEBUG` löschen.
3. `src/browser-worker/mediamarkt-task-executor.ts`
   - `syncDebug`, `flushDebugFile`, `withDebugSync` und das Spiegeln von `mediamarktDebug` in `task.config.data` entfernen (inkl. `%TEMP%\ares-mediamarkt-debug.log`-Schreiben).
4. Logdateien löschen: `%TEMP%\ares-mediamarkt-debug.log`, `%TEMP%\ares-wheel-diag.log`, `%TEMP%\ares-stderr.log`, `%TEMP%\ares-stdout.log`, `runtime-probe-logs\live-probe*.log`.
5. Danach: `npm run build:backend` + einmal Smoke-Test (Direct-Run), dann committen.
6. `tools/ares-markt-probe.js`: Task-ID `direct_markt-probe-…` **behalten** – sie aktiviert den Fast-Mode im Probe (kein Debug, sondern Test-Voraussetzung).
7. Fast-Mode (`fast_mode` in `task_browser_worker.py`) bleibt drin – das ist kein Debug, sondern der Performance-Fix für MediaMarkt.
