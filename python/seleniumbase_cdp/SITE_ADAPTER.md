# SeleniumBase Site Adapter

The SeleniumBase site adapter is enabled by default in the experimental SeleniumBase profile browser. It is read-only: it observes page structure and exposes a neutral image-grid snapshot, but it does not click or submit anything.

For an authorized test page with unusual markup, place `.ares-site-adapter.json` in that SeleniumBase profile directory or pass `siteAdapterOverrides` in the worker start payload.

Supported keys: `root`, `tiles`, `instruction`, `submit`, `complete`, `failed`.

Start from `site_adapter_overrides.example.json` and change only the selectors needed for the test page. If no overrides are supplied, the adapter uses structural discovery for visible 3x3/4x4 image grids, open shadow roots, same-origin iframes, and SeleniumBase CDP iframe element traversal.
