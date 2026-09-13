# Handover – MediaMarkt Newsletter-Problem (technisch, Standort)

Datum: 2026-09-13. Nächster Entwickler startet hier.

## Ist-Zustand

- `src/commerce/mediamarkt/release-journey.ts` – `searchFromStartPage()` (ab Zeile ~329):
  - Startseite: `await this.dismissConsent(page);` (Zeile ~338)
  - Suchfeld: `findSearchField()` → `bringIntoView(field)` (Zeile ~352)
  - Tippen: `GhostCursorUiInteractionHelper(page).type(field, query, ...)` (Zeile ~375)
  - `await page.waitForTimeout(600)` → `field.press("Enter", { timeout: 5_000, focus: false, submit: true })` (Zeile ~394)
- `EXCLUDED_MARKETING_SELECTOR` + `isNewsletterOrAd()` vorhanden (Zeilen ~49, ~563). Kein Klick auf Newsletter.
- Kein Hard-Stop (Cookie-Gate-Throws entfernt), kein Wheel-RPC, keine Diagnose. Build OK.

## Beobachtetes Verhalten (live, reproduzierbar)

Nach `search-typed`:
1. MediaMarkt scrollt selbst zum Footer-Newsletter (`scrollY≈5570`).
2. Pointer bleibt auf Position des letzten Klicks → „Jetzt anmelden“ unter dem Cursor (Hover-Optik, kein Klick).
3. `bringIntoView`/`scrollIntoViewIfNeeded` scrollt langsam zurück (`rpc:scroll-into-view after=15000ms`).
4. Nutzer bricht ab → Lauf endet vor dem Checkout.

Referenzen: Handover-2 Abschnitt „Newsletter-Scroll“ (scrollY=5570, Pointer-Hover), Handover-3 „Kernproblem“ + Option B, Referenzlauf `runtime-probe-logs/markt-2026-09-13T14-38-09-049Z.summary.json` (bis `checkout-opened`).

## Technische Anknüpfungspunkte

1. **Pointer-Park nach dem Tippen** (in `searchFromStartPage`, direkt nach `this.log("search typed...")`):
   ```ts
   await page.mouse.move(4, 4).catch(() => undefined);
   ```
   Native, kein Inject. Behebt den „Jetzt anmelden“-Hover-Effekt.

2. **Nativer Wheel-Scroll nach oben** (Handover-3 Option B) – RPC fehlt:
   - `src/browser-worker/seleniumbase-rpc-page.ts` – Maus-Objekt (Zeile ~164): `wheel`-Methode ergänzen → `command("rpc", { action: "mouse-wheel", deltaX, deltaY }, 5_000)`.
   - `python/seleniumbase_cdp/task_browser_worker.py` – `_mouse()` (Zeile ~1178): `"mouse-wheel"`-Action + Dispatch `Input.dispatchMouseEvent { type: "mouseWheel", deltaX, deltaY }`. Strikt gated auf `self.fast_mode` (nur `direct_markt`, Pokémon unverändert).

3. **Consent-Klick zuverlässig** (Banner rendert nach Page-Load, `dismissConsent` läuft zu früh):
   - Einmal `waitFor({ state: "visible", timeout: 5_000 })` auf `CONSENT_BUTTON_SELECTOR` (`[data-test="pwa-consent-layer-accept-all"]`, `#pwa-consent-layer-accept-all-button`, `[data-test="pwa-consent-layer-deny-all"]`), dann `dismissConsent(page)`. Kein Polling.

4. **Adresse im Checkout**: `mediamarkt-task-executor.ts` – `fillAddressSearch()` (Zeile ~503). Live-DOM:
   - Feld: `#loqate-autocomplete` / `input[data-test="loqate-autocomplete__input"]` / `name="loqate-autocomplete__input"` (role=combobox). Aktuelle Selektoren matchen nicht → Adresse wird nicht erkannt.

## Testbefehl

```
$env:ARES_MARKT_BASE_URL="https://www.mediamarkt.de"; $env:ARES_PROBE_HEADLESS="0"; node tools/ares-markt-probe.js
```

Lauf nicht abbrechen, bis `checkout-opened` / `payment-ready` erreicht (Final-Purchase-Guard ist im Probe aus).

## Regeln

- Kein JS-Inject. Kein echter Kauf (Stopp bei Review/Payment-ready). `src/commerce/pokemon-center/release-journey.ts` nicht anfassen. Nur native Locator-/CDP-Interaktion.
