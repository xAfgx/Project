# SeleniumBase CDP-WebSocket Deflate-Fix

## Problem

Der ARES-Browser-Worker nutzt SeleniumBase (`sb_cdp`) für die Browser-Steuerung.
Bei großen Seiten (z. B. `index.html` 13 MB, `new.html` 7 MB) kann die CDP-Verbindung
über die `permessage-deflate`-WebSocket-Extension einen `AssertionError` werfen:

```
File ".../websockets/extensions/permessage_deflate.py", line 193, in encode
    assert data[-4:] == _EMPTY_UNCOMPRESSED_BLOCK
AssertionError
```

Dieser Fehler tritt **während des Sendens** über die CDP-WebSocket auf und lässt den
synchronen Command-Loop des Workers hängen. Der Worker antwortet danach nicht mehr
auf RPCs (z. B. `force-captcha-poll`), und der Checkout-Flow bleibt bei der
Neuerscheinungen-/Captcha-Seite stehen.

## Ursache

SeleniumBase öffnet seine Browser-Level-CDP-WebSocket mit `websockets.connect(...)`
**ohne** `compression=None`. Dadurch verhandelt `websockets` die
`permessage-deflate`-Extension. Bei großen CDP-Payloads (Screenshot/Grid-Capture,
DOM-Transfer) schlägt das Deflate-Frame-Encoding fehl und der Fehler reißt den
Command-Loop ab.

## Fix (manuell in der installierten SeleniumBase-Lib)

### Umgebung

- SeleniumBase: **4.53.7**
- websockets: **17.1**
- Python: **3.14**
- Installationsort: `C:\Users\A\AppData\Roaming\Python\Python314\site-packages\seleniumbase\`

### Betroffene Datei 1

`site-packages\seleniumbase\undetected\cdp_driver\connection.py` (Zeile ~238)

Vorher:

```python
self.websocket = await websockets.connect(
    self.websocket_url,
    ping_timeout=PING_TIMEOUT,
    max_size=MAX_SIZE,
)
```

Nachher:

```python
self.websocket = await websockets.connect(
    self.websocket_url,
    ping_timeout=PING_TIMEOUT,
    max_size=MAX_SIZE,
    compression=None,
)
```

### Betroffene Datei 2

`site-packages\seleniumbase\undetected\cdp.py` (Zeile ~109)

Vorher:

```python
async with websockets.connect(self.wsurl) as ws:
```

Nachher:

```python
async with websockets.connect(self.wsurl, compression=None) as ws:
```

### Kern der Änderung

In beiden `websockets.connect(...)`-Aufrufen wird **ausschließlich**
`compression=None` ergänzt. Keine weiteren Logik- oder API-Änderungen. Damit wird
die `permessage-deflate`-Extension deaktiviert und der `AssertionError` vermieden.

## Warum die Änderung nicht im Projekt-Code liegt

Der Fix greift **in der installierten SeleniumBase-Library**. Die Library wird bei
`npm install` / neuem venv / `pip install --upgrade seleniumbase` wieder überschrieben.
Daher muss die Änderung nach einer Neuinstallation erneut angewendet werden.

## Anwendung nach Neuinstallation / venv-Neuaufbau

```powershell
# Pfad zur SeleniumBase-Installation ermitteln
python -c "import seleniumbase; print(seleniumbase.__file__)"
```

Danach die beiden oben genannten `websockets.connect(...)`-Aufrufe in
`undetected/cdp_driver/connection.py` und `undetected/cdp.py` um `compression=None`
ergänzen (siehe Vorher/Nachher-Diff oben).

Hinweis: Der dauerhafte Projekt-Monkey-Patch-Ansatz (`cdp_deflate_patch.py`) wurde
**bewusst entfernt**, weil er das Import-/Referenzverhalten der SeleniumBase-WebSocket
nicht zuverlässig abdeckt und so ein zweiter, konkurrierender Fix entstanden wäre.
Es gibt also nur diesen einen, direkten Lib-Fix.

## Runtime-Beweis

Mit aktivem Lib-Fix liefert `force-captcha-poll` auf der Offline-Captcha-Seite
(`http://localhost:8080/captcha.html`) wieder eine Antwort (statt zu hängen):

```
[captcha-poll] start t=98272.5
[captcha-poll] challenge_present_fast=True t=98272.5
[captcha-poll] starting heavy watchdog t=98272.5
[captcha-poll] watchdog done t=98284.0
RPC force-captcha-poll -> ok=True in 11.5s result=True
```

Vor dem Fix blieb der Aufruf bei `starting heavy watchdog` ohne Antwort stehen.
