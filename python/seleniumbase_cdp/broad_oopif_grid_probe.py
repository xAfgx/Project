from __future__ import annotations

import json
import tempfile
from pathlib import Path
from urllib.parse import quote

from seleniumbase import SB

from scope_locked_grid_site_adapter import BROAD_OOPIF_GRID_SCRIPT


HTML = """
<!doctype html>
<html>
<head>
<meta charset="utf-8">
<style>
  body { margin: 20px; font-family: sans-serif; }
  #grid {
    display: grid;
    grid-template-columns: repeat(3, 84px);
    grid-template-rows: repeat(3, 84px);
    gap: 6px;
    width: max-content;
  }
  #grid > div {
    width: 84px;
    height: 84px;
    border: 1px solid #333;
    box-sizing: border-box;
  }
</style>
</head>
<body>
  <p id="instruction">Wähle alle Bilder mit Fahrrädern aus</p>
  <div id="grid">
    <div></div><div></div><div></div>
    <div></div><div></div><div></div>
    <div></div><div></div><div></div>
  </div>
  <button id="submit">Bestätigen</button>
</body>
</html>
"""


def main() -> int:
    url = "data:text/html;charset=utf-8," + quote(HTML)
    with SB(browser="chrome", headless=True) as sb:
        sb.open(url)
        result = sb.execute_script(BROAD_OOPIF_GRID_SCRIPT)

    if not isinstance(result, dict):
        raise AssertionError(f"Broad grid probe returned non-object: {type(result).__name__}")
    if result.get("kind") != "image-grid":
        raise AssertionError("Broad grid probe did not detect the neutral DIV grid: " + json.dumps(result))
    if int(result.get("tileCount") or 0) != 9:
        raise AssertionError(f"Expected 9 tiles, got {result.get('tileCount')}")
    if (int(result.get("rows") or 0), int(result.get("columns") or 0)) != (3, 3):
        raise AssertionError(
            f"Expected 3x3 grid, got {result.get('rows')}x{result.get('columns')}"
        )
    if str(result.get("origin") or "") != "direct-children":
        raise AssertionError(f"Expected direct-children origin, got {result.get('origin')!r}")
    sources = list(result.get("sources") or [])
    if len(sources) != 9 or any(sources):
        raise AssertionError(f"Expected nine blank DOM sources for screenshot-crop fallback, got {sources!r}")
    debug = result.get("debug") if isinstance(result.get("debug"), dict) else {}
    if int(debug.get("directChildCandidates") or 0) < 1:
        raise AssertionError(f"Missing direct-child diagnostics: {debug!r}")

    print(
        "PASS: broad structural grid detection resolves a neutral 3x3 DIV grid "
        "through direct-children geometry with blank DOM sources."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
