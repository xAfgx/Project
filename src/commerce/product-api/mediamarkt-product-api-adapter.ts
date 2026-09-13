import { spawnSync } from "child_process";
import * as fs from "fs";
import * as path from "path";
import type { CommerceShop } from "../platforms";
import type { ProductObservation, ProductQuery } from "../../monitor/models";
import type { CommerceProductApiAdapter, ProductApiProbeResult } from "./types";

/**
 * MediaMarkt public GraphQL (Apollo persisted queries). Requests go through a
 * tiny Python/curl_cffi sidecar so they carry a Chrome TLS fingerprint without
 * launching a browser. Polling this is cheap: one small HTTPS GET per interval.
 */
const GRAPHQL_URL = process.env["ARES_MM_GRAPHQL_URL"]?.trim() || "https://www.mediamarkt.de/api/v1/graphql";
const SEARCH_HASH = process.env["ARES_MM_SEARCH_HASH"]?.trim() || "a1fdd4211e8a9179c59e7bb9e335db1e035fdbd7c7b3ba744539e3a1654d3056";
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

interface GraphqlReply {
  ok: boolean;
  status?: number;
  data?: Record<string, unknown>;
  error?: string;
}

function resolveHelper(): string {
  const resourcesPath = (process as NodeJS.Process & { resourcesPath?: string }).resourcesPath || "";
  const candidates = [
    process.env["ARES_MM_GRAPHQL_HELPER"]?.trim(),
    path.join(process.cwd(), "python", "seleniumbase_cdp", "mm_graphql.py"),
    path.join(__dirname, "../../../python/seleniumbase_cdp/mm_graphql.py"),
    resourcesPath ? path.join(resourcesPath, "python", "seleniumbase_cdp", "mm_graphql.py") : undefined
  ].filter((value): value is string => Boolean(value));
  const resolved = candidates.find(candidate => fs.existsSync(candidate));
  if (!resolved) throw new Error("mm_graphql.py wurde nicht gefunden.");
  return resolved;
}

function callGraphql(request: Record<string, unknown>): GraphqlReply {
  const script = resolveHelper();
  const result = spawnSync(process.env["ARES_PYTHON_EXECUTABLE"]?.trim() || "python", ["-u", script], {
    input: JSON.stringify(request),
    encoding: "utf8",
    windowsHide: true,
    timeout: 60_000,
    env: { ...process.env, PYTHONUNBUFFERED: "1", PYTHONUTF8: "1", PYTHONIOENCODING: "utf-8" }
  });
  if (result.error) return { ok: false, error: result.error.message };
  const lines = String(result.stdout ?? "").trim().split(/\r?\n/);
  const line = lines[lines.length - 1] ?? "";
  try {
    return JSON.parse(line) as GraphqlReply;
  } catch {
    return { ok: false, error: `Ungültige Helfer-Antwort: ${line.slice(0, 300)}` };
  }
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
}

function scalar(value: unknown): string | number | boolean | undefined {
  return typeof value === "string" || typeof value === "number" || typeof value === "boolean" ? value : undefined;
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
    const reply = callGraphql({
      url: GRAPHQL_URL,
      operation: "SearchV4",
      hash: SEARCH_HASH,
      cacheable: true,
      clientName: CLIENT_NAME,
      clientVersion: CLIENT_VERSION,
      cookieFile: process.env["ARES_MM_COOKIE_FILE"]?.trim() || undefined,
      variables: this.variables(term),
      pwa: PWA
    });
    if (!reply.ok) return { products: [], error: reply.error ?? `HTTP ${reply.status ?? "?"}` };
    const search = asRecord(reply.data?.["searchV4"]);
    const products = Array.isArray(search["products"]) ? search["products"] as unknown[] : [];
    const total = Number(search["totalProducts"]);
    return { products, total: Number.isFinite(total) ? total : undefined };
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
    return observations;
  }
}
