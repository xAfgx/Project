#!/usr/bin/env python3
"""Standalone MediaMarkt API monitor (no browser).

Polls the public MediaMarkt GraphQL API with a Chrome TLS fingerprint via
curl_cffi and reports new products / availability changes per category. This is
the lightweight, low-power alternative to running a browser: one small HTTPS
GET per watch per interval.

Categories are configured in WATCHES below. Each watch has a name and a query,
which is either a free-text search ("starlink", "pokemon") or a brand query
("ent_brand_dpl_pokemon_neuheiten_mm").

Environment (all optional):
  MM_INTERVAL_SECONDS   base poll interval (default 30)
  MM_JITTER_PERCENT     random jitter in percent (default 30)
  MM_COOKIE_FILE        JSON file with [{name,value,domain,path}, ...]
  MM_STATE_FILE         state file (default: mm_monitor_state.json)
  DISCORD_WEBHOOK       post change embeds if set
"""

import json
import os
import random
import time
import uuid
from pathlib import Path

from curl_cffi import requests

GRAPHQL_URL = os.environ.get("MM_GRAPHQL_URL", "https://www.mediamarkt.de/api/v1/graphql")
SEARCH_HASH = os.environ.get("MM_SEARCH_HASH", "a1fdd4211e8a9179c59e7bb9e335db1e035fdbd7c7b3ba744539e3a1654d3056")
CLIENT_NAME = os.environ.get("MM_CLIENT_NAME", "pwa-client-pqm")
CLIENT_VERSION = os.environ.get("MM_CLIENT_VERSION", "8.484.0")
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36"
)

WATCHES = [
    {"name": "Pokemon Neuheiten", "query": "ent_brand_dpl_pokemon_neuheiten_mm"},
    {"name": "Starlink", "query": "starlink"},
]

STATE_FILE = Path(os.environ.get("MM_STATE_FILE", Path(__file__).with_name("mm_monitor_state.json")))
COOKIE_FILE = os.environ.get("MM_COOKIE_FILE", "").strip()
DISCORD_WEBHOOK = os.environ.get("DISCORD_WEBHOOK", "").strip()
INTERVAL = float(os.environ.get("MM_INTERVAL_SECONDS", "30"))
JITTER = float(os.environ.get("MM_JITTER_PERCENT", "30"))


def log(message):
    print(f"[{time.strftime('%H:%M:%S')}] {message}", flush=True)


def load_state():
    if not STATE_FILE.exists():
        return {}
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_state(state):
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(STATE_FILE)


def load_cookies():
    if not COOKIE_FILE or not Path(COOKIE_FILE).exists():
        return None
    try:
        cookies = json.loads(Path(COOKIE_FILE).read_text(encoding="utf-8"))
        return cookies if isinstance(cookies, list) and cookies else None
    except Exception:
        return None


def graphql(query):
    is_brand = query.lower().startswith("ent_brand_")
    variables = {
        "query": query,
        "page": 1,
        "locale": "de-DE",
        "salesLine": "Media",
        "filters": ["marketplace:MediaMarkt"],
    }
    if is_brand:
        variables["brandCategory"] = "neuheiten"
    params = {
        "operationName": "SearchV4",
        "variables": json.dumps(variables, separators=(",", ":")),
        "extensions": json.dumps(
            {
                "persistedQuery": {"version": 1, "sha256Hash": SEARCH_HASH},
                "pwa": {"captureChannel": "DESKTOP", "salesLine": "Media", "country": "DE", "language": "de"},
            },
            separators=(",", ":"),
        ),
    }
    headers = {
        "accept": "*/*",
        "content-type": "application/json",
        "x-mms-salesline": "Media",
        "x-mms-country": "DE",
        "x-mms-language": "de",
        "apollographql-client-name": CLIENT_NAME,
        "apollographql-client-version": CLIENT_VERSION,
        "user-agent": USER_AGENT,
        "x-operation": "SearchV4",
        "x-cacheable": "true",
        "x-flow-id": str(uuid.uuid4()),
    }
    response = requests.get(
        GRAPHQL_URL,
        params=params,
        headers=headers,
        cookies=load_cookies(),
        impersonate="chrome",
        timeout=30,
    )
    if response.status_code != 200:
        raise RuntimeError(f"HTTP {response.status_code}: {response.text[:200]}")
    payload = response.json()
    if payload.get("errors"):
        raise RuntimeError(f"GraphQL: {json.dumps(payload['errors'], ensure_ascii=False)[:300]}")
    return (payload.get("data") or {}).get("searchV4") or {}


def map_product(item):
    aggregate = item.get("productAggregate") or {}
    product = aggregate.get("product") or {}
    cofr = item.get("cofrProductAggregate") or {}
    core = cofr.get("cofrCoreFeature") or {}
    online = cofr.get("cofrOnlineStatusFeature") or {}
    delivery_feature = cofr.get("cofrDeliveryFeature") or {}
    delivery = delivery_feature.get("delivery") or {}
    price_feature = cofr.get("cofrPriceFeature") or {}
    price_data = price_feature.get("price") or {}

    core_id = str(core.get("id") or "")
    pid = str(aggregate.get("productId") or product.get("id") or core_id.split(":")[-1] or "").strip()
    title = str(core.get("productName") or product.get("title") or "").strip()
    if not pid or not title:
        return None

    relative = str(core.get("urlRelative") or product.get("url") or "")
    url = relative if relative.startswith("http") else f"https://www.mediamarkt.de{relative}"
    available = online.get("isAvailableAndBuyable") is True or online.get("isAvailableForPickup") is True

    return {
        "id": pid,
        "name": title,
        "url": url,
        "price": price_data.get("amount"),
        "currency": price_feature.get("currency") or "EUR",
        "available": available,
        "onlineStatus": online.get("onlineStatus"),
        "deliveryStatus": delivery.get("displayStatus"),
    }


def discord(title, description, fields=None, url=""):
    if not DISCORD_WEBHOOK:
        return
    embed = {"title": title, "description": description, "color": 15158332}
    if url:
        embed["url"] = url
    if fields:
        embed["fields"] = fields
    try:
        requests.post(DISCORD_WEBHOOK, json={"embeds": [embed]}, timeout=20)
    except Exception as exc:
        log(f"DISCORD Fehler: {exc}")


def check_watch(state, watch):
    name = watch["name"]
    search = graphql(watch["query"])
    products = [p for p in (map_product(item) for item in (search.get("products") or [])) if p]
    known = state.setdefault(name, {})
    first_run = len(known) == 0

    for product in products:
        pid = product["id"]
        previous = known.get(pid)
        known[pid] = product

        if first_run or previous is None:
            continue

        if not previous.get("available") and product.get("available"):
            log(f"{name}: VERFÜGBAR -> {product['name']}")
            discord(
                f"{name}: verfügbar",
                product["name"],
                [
                    {"name": "Preis", "value": str(product.get("price")), "inline": True},
                    {"name": "Status", "value": str(product.get("onlineStatus")), "inline": True},
                ],
                url=product.get("url", ""),
            )
        elif previous.get("onlineStatus") != product.get("onlineStatus"):
            log(f"{name}: {product['name']} -> {product.get('onlineStatus')}")

    if first_run:
        log(f"{name}: Baseline mit {len(products)} Produkten gespeichert.")


def main():
    log(f"MediaMarkt API Monitor | {len(WATCHES)} Watches | Intervall {INTERVAL:.0f}s ±{JITTER:.0f}% | Browser: nein")
    while True:
        state = load_state()
        for watch in WATCHES:
            try:
                check_watch(state, watch)
            except Exception as exc:
                log(f"{watch['name']}: Fehler: {exc}")
            time.sleep(1.5)
        save_state(state)

        delay = INTERVAL * (1 + random.uniform(-JITTER, JITTER) / 100)
        time.sleep(max(5.0, delay))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print()
        log("Monitor gestoppt.")
