# HANDOVER – MediaMarkt Modul (Live-Debug-Stand 13.09.2026, ~14:20)

Hallo und danke, dass du übernimmst! Hier ist alles, was du brauchst, um das MediaMarkt-Modul ab dem aktuellen Punkt sauber zu Ende zu bringen – der Offline-Flow und der Pokémon-Pfad laufen bereits, offen ist nur noch der letzte Live-Feinschliff beim Add-to-Cart.

Workspace: `C:\Users\A\Desktop\aresUpdatedares` · Git HEAD: `53af228` + uncommittete Änderungen (nicht committen ohne Auftrag).
Build nach jeder src-Änderung: `npm run build:backend` (Python nur `py_compile` nötig, wird frisch gespawnt).

## 0. WICHTIG für den nächsten AI – Live-Test-Regeln
- **ARES komplett beenden + neu starten**, sonst läuft der alte, langlebige Browser-Worker-Child weiter und der neue Code greift nicht.
- Live-Task: UI → Modul **MediaMarkt** → Modus **„Direkt zum Checkout"** → Task-ID beginnt mit `direct_...` (Monitor-Modus `monitor_...` startet NUR HTTP-Monitor, keinen Browser!).
- Frisches Profil = Cookie-Banner kommt; bestehendes Profil = Banner schon akzeptiert.
- Debug: `%TEMP%\ares-mediamarkt-debug.log` (Ringpuffer, alle 2s geflusht; zusätzlich `mediamarktDebug` im Task-Data in `%APPDATA%\ares-task-orchestrator\ares.sqlite`). Einträge: `page-top`, `search-field(-inview/-covered)`, `consent-click/-clicked/-py-start/-py-done`, `search-typed`, `search-suggestion`, `search-submitted`, `product-click`, `add-to-cart-click`, `add-to-cart-no-confirm`, `add-to-cart-failed`, `minibasket-primary/-fallback`, `checkout-step`.
- Der Debug ist **temporär** (im Code als `TEMPORARY LIVE DEBUG` markiert) und soll nach Live-Validierung entfernt werden.

## 1. Was FERTIG ist (offline verifiziert)
- **Offline-Flow** (Harness): Suche → Produkt → Cart → Overview → Shipping → Payment → `paymentReady:true`, Kartenfelder `cardNumber, expiry, securityCode, holderName`, Guard `blocked`. Gesamt ~47s inkl. Karten-Tippen.
- **Pokémon-Flow** (Harness): Queue → hCaptcha (SigLIP) → `/de-de` → Consent (OneTrust) → 1. Klick trifft → Cart → intl-checkout → `payment-ready`.
- Consent **MediaMarkt live**: `search-field-covered` → Klick auf `[data-test="pwa-consent-layer-accept-all"]` in ~1.8s ✅ (12:18-Lauf).
- Suche live: tippen → Vorschlag `#popup-list-item-0` geklickt → Ergebnisseite ✅.
- Produktklick live ✅.

## 2. OFFENE Live-Probleme (genau hier weitermachen)
1. **Add-to-Cart live verpufft**: Button liegt bei `y≈953` (Viewport 747, unter dem Fold). Ohne Vor-Scroll → Klick trifft nicht (`add-to-cart-no-confirm miniBasket:false`), Retry traf Empfehlungs-Kacheln → anderes Produkt (User-Beobachtung 12:18-Lauf). **Fix gerade eingebaut, noch nicht live verifiziert**: `bringIntoView(add, 6000)` vor Klick + vor Retry; `hasMiniBasket` = Erfolg, kein Retry wenn Modal offen.
2. **Seite scrollt während Suche nach unten** (`scrollY=5570`, Vorschlag bei `y=-5478`); Ursache unbekannt (evtl. MediaMarkt-JS/Sticky-Newsletter). Bricht den Flow nicht, sieht aber schlecht aus und kostet Zeit.
3. **Live-Tempo**: Consent ok (~2s), aber pro RPC auf der Live-Seite hohe Latenz; Produktklick→Add-to-Cart ~14–26s. Redundante Scans wurden entfernt (kein Challenge-/Consent-Scan vor Add-to-Cart, Load-Wait 5s, Pausen 300ms).
4. **Mini-Basket-Modal live nie beobachtet**, weil Klick verpuffte. Marker sind aus dem echten HTML bekannt (s.u.).

## 3. Echte Live-Handles (aus User-HTML, verifiziert)
- **Consent MediaMarkt**: Container `[data-test="mms-privacy-layer"]`, Form `[data-test="pwa-consent-layer"]`, Buttons `[data-test="pwa-consent-layer-accept-all"]` (`#pwa-consent-layer-accept-all-button`), `-deny-all`, `-save-settings`. Text: „Alle zulassen"/„Alle ablehnen".
- **Consent OneTrust (Pokémon)**: `#onetrust-banner-sdk`, `#onetrust-accept-btn-handler` („Alle Cookies akzeptieren"), `#onetrust-reject-all-handler` („Nur notwendige Cookies"), `#onetrust-pc-btn-handler`.
- **Suche**: `#search-form` (role=combobox, `aria-controls=search-popup`), KEIN Submit-Button; Vorschlag `li#popup-list-item-0`.
- **PDP Add-to-Cart**: `#pdp-add-to-cart-button` + `data-test="cofr-add-to-basket-button a2c-Button"` + `aria-label="In den Warenkorb …"` (2 Treffer, einer unsichtbar → ersten **sichtbaren** nehmen).
- **Mini-Basket-Modal**: `#mms-styled-modal-inner-wrapper`, `[data-test="pdp-minibasket-headline-success"]`, Close `button[aria-label="Schließen"]` (svg `data-test="modal-close-button"`), Primary `[data-test="mms-router-link-mms-pre-checkout-modal-primary-button"]` → `/de/checkout` („Zum Warenkorb"), Secondary `[data-test="mms-pre-checkout-modal-secondary-button"]` („Weiter einkaufen"). Enthält „Für dich empfohlen"-Produktkarten → **blinder Retry klickt die!**
- **Newsletter**: `[data-test="mms-newsletter-subscription"]`, Link `a[href*="guest-newsletter"]` („Jetzt anmelden"), sticky unten.
- **Cart/Checkout**: `[data-test="checkout-continue-button"]` („Zur Kasse gehen"/„Fortfahren und bezahlen"/„Weiter"); Payment `#cardNumber`, `#expMonth`, `#checkNumber`, `#cardholderName`.

## 4. MediaMarkt-Modul (Dateien)
- `src/commerce/mediamarkt/release-journey.ts` – Journey: `discover` (Vorschlag → direkt Produktseite; sonst Ergebnis-Scan mit Marketing-Ausschluss; native Klicks), `addToCart` (Button-Suche, bringIntoView, Klick, `waitForCart` inkl. Mini-Basket, Retry mit bringIntoView), `openCheckout` (Mini-Basket-Primary → Fallback Close+`/de/checkout`; Continue-Loop), `advanceCheckout`, `submitOrder` (Guard), `isOrderConfirmed`, `dismissConsent` (CMP-Handles + 4s-Watch über `isCovered`), `bringIntoView`, `hasMiniBasket`, `EXCLUDED_MARKETING_SELECTOR` (Newsletter/Teaser/Carousel/Ads), Debug-Ringpuffer.
- `src/browser-worker/mediamarkt-task-executor.ts` – Direct-Lane (kein Monitor/Queue): Context → Discovery-Loop → Cart → Checkout → Prep (`SemanticCheckoutPreparer maxAttempts:1`; `CheckoutPaymentPreparer` mit `securityCodeSelectors:['input[id*="checknumber" i]']`, `cardFillOrder:[cardNumber,expiry,securityCode,holderName]`) → Guard-Loop; Cancel/Close; `syncDebug`+`flushDebugFile`; langsames Scroll-Profil (`setScrollProfile`).
- `src/browser-worker/worker.ts` – `platform === "mediamarkt"` Branch, Executor-Instanz, Cancel/Shutdown/Permission; `PYTHONUTF8=1`/`PYTHONIOENCODING=utf-8` (fixte Live-Abbruch `'charmap' codec … ✓`).
- `src/electron/main.ts` – `commerceExecutor.register("mediamarkt", paymentAwareBrowserWorker)`; `PYTHONUTF8` global.
- `src/app/app.component.ts` – Modul MediaMarkt (`defaultShop`, `directLane:true`), Plattform-Label, `createTask` Direct-Lane (profilId+searchTerm, KEIN productCriteria → kein Monitor); Logo; Composer-Shop fix.
- `src/app/app.component.html` – MediaMarkt-Logo (SVG), Shop-Feld-Logik.
- `src/browser-worker/checkout-payment-preparer.ts` – opt-in `securityCodeSelectors`/`cardFillOrder`, Defaults = alt (Pokémon unverändert).
- `src/browser-worker/semantic-checkout-preparer.ts` – `maxAttempts` opt-in (Default 6).
- `src/browser-worker/types.ts` / `seleniumbase-rpc-page.ts` – `press`, `dismissConsentPopups`, `setScrollProfile`, `viewportSize`.
- Python: `task_browser_worker.py` (`press`-Action, `dismiss-consent`, `set-scroll-profile`, `viewport-size`), `task_browser_worker_oopif_impl.py` (Scroll-Profil, Sticky-Header-Fix), `visual_interaction_runtime.py`+`seleniumbase_adapter.py` (`dismiss_consent_popup`, synchron!).

## 5. Pokémon – additive Änderungen (funktionieren)
- `src/commerce/pokemon-center/release-journey.ts`: Karte erst `scrollIntoViewIfNeeded()` (smooth), dann `anchor.click()` (native Live-Box; Ghost-Pfad intern) → 1. Klick trifft.
- `src/browser-worker/early-gate-task-executor.ts`: `dismissStorefrontConsent(page)` nach Queue+hCaptcha vor Discovery (nur `#onetrust-*`/`pwa-consent-layer-*`, nur wenn sichtbar). Captcha-/Queue-Logik unangetastet.
- Offline: OneTrust-Banner wird auf `/de-de` injiziert (`C:\Users\A\Downloads\SEITEN\pokemoncenter_offline_flow\server.py`, Entfernung im gehashten WIRE_JS wegen CSP).

## 6. Harnesses / Tools
- MediaMarkt: `C:\Users\A\Downloads\markt\server.py`, Port **18090** (`$env:ARES_MARKT_PORT="18090"; python server.py`), Start detached (`cmd /c start "" /B python server.py`), sonst killt Abbrechen den Server. Routen: `/`, `/search/airtag`, `/search/pokemon`, `/de/product/…`, `/de/cart`, `/de/checkout/{overview,shipping,payment}`, `/login`, `/consent-test` (MediaMarkt-Banner), `/consent-test-onetrust` (OneTrust-Banner), `/de/search.html?query=` → 302. WIRE_JS (CSP-Hash): Suche/Enter, Consent-Entfernung, Produkt/Cart-Navigation; Iframes entfernt; Socket-Timeout 15.
- Pokémon: `C:\Users\A\Downloads\SEITEN\pokemoncenter_offline_flow\server.py`, Port 8080/18080 (`ARES_OFFLINE_PORT`).
- Probes: `tools/ares-markt-probe.js` (`ARES_MARKT_BASE_URL`, `ARES_PROBE_HEADLESS=0`, `ARES_PROBE_CONCURRENT=1`), `tools/ares-runtime-probe.js` (Pokémon).
- Log-Auslesen: `python "C:\Users\A\AppData\Local\Temp\opencode\dump-ares-debug.py"` (Task + mediamarktDebug) bzw. `dump-task.py <taskId>`.

## 7. Bekannte Ursachen/Erkenntnisse
- `isVisible` ist CSS-only → Suchfeld „gefunden", obwohl Seite unten (`y=-5558`); Fix: `bringIntoView` + `isCovered` (elementFromPoint).
- Ghost-Klick auf Karten verpufft bei nachladenden Grids (Box veraltet) → native Locator-Klicks (lösen Live-Box, spielen Ghost-Pfad intern).
- `press("Home")` auf `body` war gefährlich: Basis-`_native_focus_locator` **klickt** als Fallback (traf iPhone-Teaser). Entfernt.
- Generischer `button[type="submit"]` traf Newsletter → nur noch `form:has(input[type=search])`.
- 120ms-Sichtbarkeits-Timeouts zu kurz für Live → 400–500ms.
- Mini-Basket = Erfolg, **niemals** blind erneut klicken (Empfehlungskacheln).

## 8. Nächste Schritte (Empfehlung)
1. Live testen mit dem aktuellen Build (bringIntoView vor Add-to-Cart). Debug lesen: kommt `mini-basket confirmed` + `minibasket-primary`?
2. Falls Klick weiter verpufft: vor dem Klick prüfen, ob die Button-Mitte per `elementFromPoint` frei ist (`isCovered(add)`), sonst erst Sticky-Bar/Overlay schließen; danach klicken.
3. Scroll-nach-unten-Phänomen der Live-Suche prüfen (Debug `search-suggestion` y/scrollY).
4. Nach erfolgreicher Live-Validierung: temporären Debug entfernen (`TEMPORARY LIVE DEBUG` in `release-journey.ts` + `mediamarkt-task-executor.ts`), Build, ggf. committen.
