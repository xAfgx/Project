# HANDOVER 3 – MediaMarkt Live-Stand (13.09.2026, ~16:00)

Fortsetzung von `HANDOVER-MEDIAMARKT-LIVE-2.md`. Diese Session hat den Such-Flow weiter hartgemacht, aber **live führt der Weg weiterhin über das Vorschlags-Popup und der blockiert**: Tippen funktioniert, danach blockiert die Seite den Main-Thread für 10–25 s+, alle Sonden laufen in Timeouts, der Vorschlags-Klick wird nicht zuverlässig erreicht. Stand: `npm run build:backend` ist um ~15:56 durchgelaufen, **dist und Quellcode sind synchron**. Pokémon bleibt unverändert (Fast-Mode nur `direct_`, `task_browser_worker.py:1286`).

## 1. Was in dieser Session geändert wurde (nur `src/commerce/mediamarkt/release-journey.ts`)

- **Nach dem Tippen** (`searchFromStartPage`, ab Zeile ~392): Pointer wird mit `page.mouse.move(4,4)` neutral geparkt (liegt nach Consent-Klick mitten auf der Seite → Newsletter-Hover-Optik), dann 600 ms Warten, `before = page.url()` ohne RPC-Sonde.
- **`clickFirstSuggestion()`** (~Zeile 445): Selector-Reihenfolge nach Live-Beweis: `#popup-list-item-0` zuerst, dann `[id^="popup-list-item-"]`, `#search-popup [role="option"]`, `[role="listbox"] [role="option"]`, `#search-popup li a`, `[data-test*="suggestion" i]`. Pro Selector eine `isVisible(1500)`-Sonde, dann ein einziger `option.click({ timeout: 6000 })`. Explizite Bestätigungs-Logs: `search suggestion-clicked …` / `search suggestion-click-missed …`.
- **Entfernt worden (waren schädlich):** `bringIntoView`-/`clickControlCenter`-Kette für Vorschläge und je Vorschlag `describe()` – das hat den Klick von ~2 s auf 28–44 s gestreckt und den sichtbaren Schnecken-Scroll erzeugt (`rpc:scroll-into-view after=15000ms`).
- **Kein JS-Inject irgendwo** (ein zwischenzeitlicher `scrollIntoView`-Versuch per evaluate wurde zurückgebaut, nutzerseitige Vorgabe). Nur Treiber-Mechanik + read-only Geometrie wie zuvor.
- `clickControlCenter` hat nur einen optionalen `label`-Parameter (Default `"add-to-cart"`) bekommen; Verhalten für Add-to-Cart identisch.

## 2. Live-Läufe dieser Session (Debug-Ring `%TEMP%\ares-mediamarkt-debug.log`)

| Zeit | Ergebnis |
|---|---|
| 13:41 | **Kompletter Best-Case**: Tippen → 1×`rpc:count`-Timeout (5 s) → später kommt eine Sonde durch → nackter Locator-Klick auf das `li` klappt **aus dem Newsletter-Offset heraus** (`y=-5478`) in 1,9 s → suggest-URL → Produkt → `cart-confirmed` → Mini-Basket → `/de/checkout`. Abbruch vor Wizard. |
| 13:47 | Tippen → 2×`rpc:count` 5 s → **alle 6 `is-visible`-Sonden timeouten** (~24 s nach Tippen) → Enter tut nichts → URL bleibt Startseite → Code fällt auf URL-Goto-Fallback (abgelehnt). |
| 15:53 | Tippen → `isVisible` auf `#popup-list-item-0` okay, aber meine (damalige) Scroll-Kette verbrät 15 s → Locator-Fallback klickt doch bei +44 s → suggest-URL. Sichtbar: erst runter zum Newsletter, dann langsame Schnecken-Bewegung wieder hoch. Abgebrochen. |
| 15:55 | Tippen → 2×`rpc:count` 5 s → 7×`is-visible` timeouts (~24 s) → Enter ohne Wirkung. Abgebrochen. |

**Kernproblem in einem Satz:** Nach dem Tippen (a) scrollt MediaMarkts eigenes JS zum Footer-Newsletter (Popup-Item landet bei `y≈-5478`, `scrollY≈5570`) und (b) blockiert das Vorschlags-Rendering den Main-Thread für ~10–25 s, sodass **jedes** CDP-Evaluate (`count`, `isVisible`, `evaluate`, `scrollIntoViewIfNeeded`) in den 5 s harten Worker-RPC-Timeout (`seleniumbase-rpc-page.ts`, alle mouse/count-Kommandos 5 000–10 000 ms) oder die kurzen Sichtbarkeits-Sonden läuft. Native Eingabe-Events (`type`, `mouse.click`, `press`) überleben das, Sonden nicht. Bewiesener Klick (13:41): nackter `locator.click` auf den Vorschlag, sobald **eine** `isVisible`-Sonde durchkommt — das ist Glück/Timing, keine Garantie.

## 3. Was NICHT mehr probiert werden soll (nutzerseitig abgelehnt)

1. **Kein JS-Inject** auf der Seite (kein `scrollIntoView`/`scrollTo` per evaluate, keine Seiten-Skripte).
2. **Kein direktes URL-Goto auf die Such-URL** als Suchweg (existiert nur als letzten Fallback in `runSearch`).
3. Keine long-running `bringIntoView`-Ketten im Suchfeld-Bereich (Schnecken-Scroll, sichtbar hässlich).

## 4. Optionen für den nächsten Entwickler (alle ungetestet, alle ohne Inject)

A. **Blinder nativer Klick nach dem Tippen:** Der `rpc:type`-Call kommt durch (~2 s). Popup-Geometrie ist bekannt: Feld `x=466,y=12,w=329` → erster Vorschlag mittig bei ca. `x=630, y=95–115` (solange die Seite noch nicht gescrollt hat). ~1,5–2 s nach Tippen `page.mouse.click(630, 100)` ohne jede Sonde. CDP-Input wird auch bei blockiertem Main-Thread eingereiht und bei Unblock ausgeführt. Risiko: Der Newsletter-Scroll war schon vorher weg → Klick bei (630,100) trifft toten Header-Bereich; dann genau ein Retry nach `waitForTimeout`.
B. **Native Wheel-Scroll:** Der RPC-Layer hat **kein** `mouse.wheel` (`seleniumbase-rpc-page.ts:162–166` nur move/down/up/click). `rpc:mouse-wheel` im Worker via `Input.dispatchMouseEvent { type: "mouseWheel", deltaY: -6000 }` ergänzen (strict gated auf `fast_mode`/`direct_`, Pokémon-Verhalten byte-identisch lassen) → nach dem Tippen nativ zurück nach oben scrollen, dann Option A oder erste durchkommende Sonde. Das ist der einzige Weg, das Newsletter-Problem ohne Inject unsichtbar zu machen.
C. **Längere Sonde statt kürzer:** `rpc:count`/`is-visible` im Such-Phase-Budget auf 15–20 s heben (Hard-Timeout im Python-Worker). Bei 13:41 kam die Sonde nach ~9,5 s durch — einfaches Durchhalten würde heute schon reichen. Aber: alle Läufe 13:47/15:55 zeigen auch 25 s Timeout-Grenze, also kein Selbstläufer.
D. **Tastatur:** `ArrowDown` + `Enter` (nativ, überlebt Blockade) ist nie getestet; `Enter` allein reicht live nicht zuverlässig.

Empfehlung: **B + A** kombiniert (Wheel rauf, blinder Center-Klick auf bekannte Popup-Koordinate), Fallback C für die Sonden.

## 5. Offene Punkte aus LIVE-2, unverändert

1. **Wizard „Zur Kasse gehen“**: Fix im Quellcode (`CONTINUE_TESTID` mit `checkout-continue-*-enabled`-Varianten, `pickClickable()` ~Zeile 930, Center-Klick), in dieser Session **nicht mehr live geprüft** (Suche nicht mehr passiert). 13:41-Lauf war wieder bis `/de/checkout`.
2. Add-to-Cart/Mini-Basket/Checkout: mehrfach live bewiesen (zuletzt 13:42, `cart-confirmed signal=mini-basket`).
3. Debug-Reste entfernen: **erst nach erfolgreicher Live-Validierung**, siehe LIVE-2 Abschnitt 7 (Liste bleibt gültig).
4. `settleAfterNavigation` weiter ungenutzt.

## 6. Sofort-Referenz

```
# Build (der letzte von dieser Session ist current, Stand 15:56):
npm run build:backend

# Live-Probe (Final-Purchase-Guard ist IMMER aus, kauft nicht echt):
$env:ARES_MARKT_BASE_URL="https://www.mediamarkt.de"; $env:ARES_PROBE_HEADLESS="0"; node tools/ares-markt-probe.js

# Logs:
%TEMP%\ares-mediamarkt-debug.log    # Journey-Ringpuffer (search-suggestion, cart-confirmed, checkout-step)
%TEMP%\ares-probe-live.log          # Stderr des letzten Probe-Laufs
python "%LOCALAPPDATA%\Temp\opencode\dump-ares-debug.py"
```

Dateien: `src/commerce/mediamarkt/release-journey.ts` (Such-Flow ab ~Zeile 328, `clickFirstSuggestion` ~445), `src/browser-worker/seleniumbase-rpc-page.ts` (RPC-Timeouts/Maus), `python/seleniumbase_cdp/task_browser_worker.py` (Fast-Mode Zeile 1286, `rpc:count` Hard-Timeout 5 s).
