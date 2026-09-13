import * as os from "os";
import * as path from "path";
import type { CommerceShop } from "../platforms";
import type { ProductObservation, ProductQuery } from "../../monitor/models";
import type { CommerceProductApiAdapter, ProductApiProbeResult } from "./types";
import { MmGraphqlClient } from "./mm-graphql-client";

/**
 * MediaMarkt public GraphQL (Apollo persisted queries). Requests go through a
 * tiny Python/curl_cffi sidecar so they carry a Chrome TLS fingerprint without
 * launching a browser. Polling this is cheap: one small HTTPS GET per interval.
 */
const GRAPHQL_URL = process.env["ARES_MM_GRAPHQL_URL"]?.trim() || "https://www.mediamarkt.de/api/v1/graphql";
const SEARCH_HASH = process.env["ARES_MM_SEARCH_HASH"]?.trim() || "a1fdd4211e8a9179c59e7bb9e335db1e035fdbd7c7b3ba744539e3a1654d3056";
const PICKUP_HASH = process.env["ARES_MM_PICKUP_HASH"]?.trim() || "81938af9c97c7bf5419b70d29819aba4cdee672fba4b020a5178dadd3b0cd979";
const CLIENT_NAME = process.env["ARES_MM_CLIENT_NAME"]?.trim() || "pwa-client-pqm";
const CLIENT_VERSION = process.env["ARES_MM_CLIENT_VERSION"]?.trim() || "8.484.0";

const COFR_CONFIG = {
  isEnabled: true,
  baseDomain: "https://www.mediamarkt.de",
  channel: "DESKTOP",
  isLegacyDataExcluded: false,
  features: {
    badges: { isFreeShippingBadgeIncluded: false },
    crossSalesLine: { isEnabled: true, isOutputForced: false },
    onlineStatus: { isPermanentlyNaIndexEnabled: true },
    pickup: { isStrictPickupDisplayStatusEnabled: false },
    price: {
      strikePriceTypes: [
        { strikePriceType: "lop" },
        { strikePriceType: "rrp", shouldBeStruck: true, showDiscountBadge: true, isLegalTextInlineAllowed: false }
      ],
      isBasePriceRequiredFlagRespected: false,
      isDiscountLabelEnabled: true,
      isDiscountPercentageShown: true,
      isDisplayPriceWithStrikePriceRrpThemed: true,
      isLongerStrikePricePrefixAllowed: false,
      isPromoPriceFiltered: true,
      isPromoPriceUsedAsDisplayPriceInApp: false,
      isHistoryChartEnabled: false,
      discountPercentageMinimum: 10,
      discountPercentageMinimumFractionDigits: 0
    },
    delivery: { isDeliveryStatusByEarliestDateEnabled: true, isLocationSourcingEnabled: true, isLocationSourcingMarketplaceEnabled: true },
    refurbishedGoods: { isEnabled: true }
  }
};

const PWA = {
  captureChannel: "DESKTOP",
  salesLine: "Media",
  country: "DE",
  language: "de",
  globalLoyaltyProgram: true,
  isOneAccountProgramActive: true,
  shouldInactiveContractsBeHidden: true,
  isUsingXccCustomerComponent: true,
  isCheckoutPhoneCompareActive: true
};

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
}

function scalar(value: unknown): string | number | boolean | undefined {
  return typeof value === "string" || typeof value === "number" || typeof value === "boolean" ? value : undefined;
}

/** Stores to check for pickup, from ARES_MM_STORES="498:Halstenbek,797:Hamburg EKZ". */
function pickupStores(): Array<{ id: string; name: string }> {
  const stores: Array<{ id: string; name: string }> = [];
  for (const entry of (process.env["ARES_MM_STORES"] ?? "").split(",")) {
    const [id, name] = entry.split(":").map(value => value.trim());
    if (id) stores.push({ id, name: name || id });
  }
  return stores;
}

/** Cookie jar written by the MediaMarkt warm-up (browser harvest). */
function cookieFile(): string {
  return process.env["ARES_MM_COOKIE_FILE"]?.trim() || path.join(os.tmpdir(), "ares-mm-cookies.json");
}

function absoluteUrl(value: unknown): string | undefined {
  const relative = String(value ?? "").trim();
  if (!relative) return undefined;
  if (/^https?:\/\//i.test(relative)) return relative;
  return `https://www.mediamarkt.de${relative.startsWith("/") ? "" : "/"}${relative}`;
}

function mapProduct(shop: CommerceShop, item: unknown): ProductObservation | undefined {
  const entry = asRecord(item);
  const aggregate = asRecord(entry["productAggregate"]);
  const product = asRecord(aggregate["product"]);
  const cofr = asRecord(entry["cofrProductAggregate"]);
  const core = asRecord(cofr["cofrCoreFeature"]);
  const online = asRecord(cofr["cofrOnlineStatusFeature"]);
  const deliveryFeature = asRecord(cofr["cofrDeliveryFeature"]);
  const delivery = asRecord(deliveryFeature["delivery"]);
  const priceFeature = asRecord(cofr["cofrPriceFeature"]);
  const priceData = asRecord(priceFeature["price"]);

  const coreId = String(core["id"] ?? "");
  const externalId = String(aggregate["productId"] ?? product["id"] ?? coreId.split(":").pop() ?? "").trim();
  const title = String(core["productName"] ?? product["title"] ?? "").trim();
  if (!externalId || !title) return undefined;

  const amount = Number(priceData["amount"]);
  const available = online["isAvailableAndBuyable"] === true || online["isAvailableForPickup"] === true;

  return {
    shopId: shop.id,
    platform: shop.platform,
    externalId,
    sku: externalId,
    title,
    url: absoluteUrl(core["urlRelative"] ?? product["url"]),
    available,
    price: Number.isFinite(amount) ? { amount, currency: String(priceFeature["currency"] ?? "EUR") } : undefined,
    observedAt: new Date(),
    attributes: {
      source: "mediamarkt-api",
      onlineStatus: scalar(online["onlineStatus"]),
      isAvailableAndBuyable: scalar(online["isAvailableAndBuyable"]),
      isAvailableForPickup: scalar(online["isAvailableForPickup"]),
      isInAssortment: scalar(online["isInAssortment"]),
      deliveryDisplayStatus: scalar(delivery["displayStatus"]),
      deliveryStatus: scalar(delivery["deliveryStatus"]),
      releaseDate: scalar(online["releaseDate"] ?? deliveryFeature["releaseDate"])
    }
  };
}

export class MediaMarktProductApiAdapter implements CommerceProductApiAdapter {
  readonly platform = "mediamarkt" as const;
  private readonly client = new MmGraphqlClient();
  private readonly pickupCache = new Map<string, string>();
  private pickupRefreshing = false;

  private variables(term: string): Record<string, unknown> {
    const isBrandQuery = /^ent_brand_/i.test(term);
    return {
      bypassStandardization: false,
      hasMarketplace: true,
      isCustomerBehaviorInfluenceActive: true,
      locale: "de-DE",
      salesLine: "Media",
      isRefurbishedGoodsActive: true,
      isPdpFaqSectionActive: true,
      isDemonstrationModelAvailabilityActive: true,
      isCrossLinkingActive: false,
      isPdpLoyaltyPointsActive: true,
      isRepairabilityIndexActive: false,
      query: term,
      page: 1,
      filters: ["marketplace:MediaMarkt"],
      searchExperiment: null,
      ...(isBrandQuery ? { brandCategory: "neuheiten" } : {}),
      cofrConfig: COFR_CONFIG
    };
  }

  private async request(term: string): Promise<{ products: unknown[]; total?: number; error?: string }> {
    const reply = await this.client.request({
      url: GRAPHQL_URL,
      operation: "SearchV4",
      hash: SEARCH_HASH,
      cacheable: true,
      clientName: CLIENT_NAME,
      clientVersion: CLIENT_VERSION,
      cookieFile: cookieFile(),
      variables: this.variables(term),
      pwa: PWA
    });
    if (!reply.ok) return { products: [], error: reply.error ?? `HTTP ${reply.status ?? "?"}` };
    const search = asRecord(reply.data?.["searchV4"]);
    const products = Array.isArray(search["products"]) ? search["products"] as unknown[] : [];
    const total = Number(search["totalProducts"]);
    return { products, total: Number.isFinite(total) ? total : undefined };
  }

  /** Per-store pickup status for a batch of product ids. */
  private async pickup(productIds: string[], stores: Array<{ id: string; name: string }>): Promise<Record<string, Record<string, string>>> {
    const result: Record<string, Record<string, string>> = {};
    for (const store of stores) {
      const reply = await this.client.request({
        url: GRAPHQL_URL,
        operation: "GetCofrPickupFeaturePickups",
        hash: PICKUP_HASH,
        cacheable: false,
        clientName: CLIENT_NAME,
        clientVersion: CLIENT_VERSION,
        cookieFile: cookieFile(),
        variables: { productIds, storeId: store.id, config: COFR_CONFIG },
        pwa: PWA
      });
      if (!reply.ok) continue;
      const data = reply.data ?? {};
      const aggregates = Array.isArray(data["cofrProductAggregates"])
        ? data["cofrProductAggregates"] as unknown[]
        : [];
      for (const item of aggregates) {
        const feature = asRecord(asRecord(item)["cofrPickupFeature"]);
        const featureId = String(feature["id"] ?? "");
        const productId = featureId.split(":")[2] ?? "";
        if (!productId) continue;
        result[productId] = result[productId] ?? {};
        result[productId][store.name] = String(feature["pickupStatus"] ?? feature["displayStatus"] ?? "?");
      }
    }
    return result;
  }

  /** Fire-and-forget pickup refresh; results are applied on the next search. */
  private refreshPickup(productIds: string[], stores: Array<{ id: string; name: string }>): void {
    if (this.pickupRefreshing || !productIds.length) return;
    this.pickupRefreshing = true;
    void this.pickup(productIds, stores)
      .then(statuses => {
        for (const [productId, perStore] of Object.entries(statuses)) {
          this.pickupCache.set(productId, Object.entries(perStore).map(([name, status]) => `${name}:${status}`).join("; "));
        }
      })
      .catch(() => undefined)
      .finally(() => { this.pickupRefreshing = false; });
  }

  /** Current cookie jar of the long-running curl_cffi session (all domains). */
  async sessionCookies(): Promise<Array<Record<string, unknown>>> {
    return this.client.cookies(cookieFile());
  }

  async probe(shop: CommerceShop): Promise<ProductApiProbeResult> {
    try {
      const result = await this.request("pokemon");
      return {
        platform: this.platform,
        endpoint: GRAPHQL_URL,
        reachable: true,
        publicReadable: !result.error,
        reason: result.error
      };
    } catch (error) {
      return {
        platform: this.platform,
        endpoint: GRAPHQL_URL,
        reachable: false,
        publicReadable: false,
        reason: error instanceof Error ? error.message : String(error)
      };
    }
  }

  async search(shop: CommerceShop, query: ProductQuery, limit = 50): Promise<ProductObservation[]> {
    const term = String(query.searchTerm ?? "").trim();
    if (!term) return [];
    const result = await this.request(term);
    if (result.error) throw new Error(`MediaMarkt GraphQL: ${result.error}`);
    const observations: ProductObservation[] = [];
    for (const item of result.products) {
      const observation = mapProduct(shop, item);
      if (observation) observations.push(observation);
      if (observations.length >= Math.max(1, limit)) break;
    }

    // Optional pickup availability. Opt-in (empty ARES_MM_STORES disables it).
    // It is applied from a cache and refreshed in the background, so the search
    // itself never waits for pickup and can never stall the monitor cycle.
    const stores = pickupStores();
    if (observations.length && stores.length) {
      for (const observation of observations) {
        const cached = observation.externalId ? this.pickupCache.get(observation.externalId) : undefined;
        if (cached) observation.attributes = { ...(observation.attributes ?? {}), pickup: cached };
      }
      const ids = observations.map(observation => observation.externalId ?? "").filter(Boolean);
      this.refreshPickup(ids, stores);
    }

    return observations;
  }
}
