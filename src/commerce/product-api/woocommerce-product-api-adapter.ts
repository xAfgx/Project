import type { CommerceShop } from "../platforms";
import type { ProductObservation, ProductQuery } from "../../monitor/models";
import { NodeJsonHttpClient } from "./http-json-client";
import type { CommerceProductApiAdapter, JsonHttpClient, ProductApiProbeResult } from "./types";

interface WooPrices {
  price?: string;
  regular_price?: string;
  sale_price?: string;
  currency_code?: string;
  currency_minor_unit?: number;
}

interface WooProduct {
  id?: number;
  name?: string;
  slug?: string;
  permalink?: string;
  sku?: string;
  global_unique_id?: string;
  is_in_stock?: boolean;
  low_stock_remaining?: number | null;
  prices?: WooPrices;
  type?: string;
  variation?: string;
  has_options?: boolean;
}

function normalizeBaseUrl(input: string): string {
  const raw = input.trim();
  const url = new URL(/^https?:\/\//i.test(raw) ? raw : `https://${raw}`);
  if (!/^https?:$/.test(url.protocol)) throw new Error(`Unsupported shop protocol: ${url.protocol}`);
  url.pathname = "/";
  url.search = "";
  url.hash = "";
  return url.toString().replace(/\/$/, "");
}

function slugFromProductUrl(value: string | undefined): string | undefined {
  if (!value) return undefined;
  try {
    const url = new URL(value);
    const match = url.pathname.match(/\/product\/([^/?#]+)/i);
    return match?.[1] ? decodeURIComponent(match[1]) : undefined;
  } catch {
    return undefined;
  }
}

function priceAmount(prices: WooPrices | undefined): number | undefined {
  if (!prices?.price) return undefined;
  const raw = Number(prices.price);
  if (!Number.isFinite(raw)) return undefined;
  const minor = Number.isFinite(prices.currency_minor_unit) ? Number(prices.currency_minor_unit) : 2;
  return raw / Math.pow(10, minor);
}

export class WooCommerceProductApiAdapter implements CommerceProductApiAdapter {
  readonly platform = "woocommerce" as const;

  constructor(private readonly httpClient: JsonHttpClient = new NodeJsonHttpClient()) {}

  async probe(shop: CommerceShop): Promise<ProductApiProbeResult> {
    const endpoint = `${normalizeBaseUrl(shop.baseUrl)}/wp-json/wc/store/v1/products?per_page=1`;
    try {
      const response = await this.httpClient.get<WooProduct[]>(endpoint);
      const publicReadable = response.status >= 200
        && response.status < 300
        && Array.isArray(response.data);
      return {
        platform: this.platform,
        endpoint,
        reachable: response.status > 0,
        status: response.status,
        publicReadable,
        reason: publicReadable ? undefined : `WooCommerce Store API returned HTTP ${response.status}.`
      };
    } catch (error) {
      return {
        platform: this.platform,
        endpoint,
        reachable: false,
        publicReadable: false,
        reason: error instanceof Error ? error.message : String(error)
      };
    }
  }

  async search(shop: CommerceShop, query: ProductQuery, limit = 50): Promise<ProductObservation[]> {
    const baseUrl = normalizeBaseUrl(shop.baseUrl);
    const safeLimit = Math.min(100, Math.max(1, Math.floor(limit)));
    const params = new URLSearchParams({ per_page: String(safeLimit) });

    if (query.searchTerm) params.set("search", query.searchTerm.trim());
    if (query.sku) params.set("sku", query.sku.trim());
    const slug = slugFromProductUrl(query.url);
    if (slug) params.set("slug", slug);

    const endpoint = `${baseUrl}/wp-json/wc/store/v1/products?${params.toString()}`;
    const response = await this.httpClient.get<WooProduct[]>(endpoint);
    if (response.status < 200 || response.status >= 300 || !Array.isArray(response.data)) {
      throw new Error(`WooCommerce Store API returned HTTP ${response.status}.`);
    }

    const observedAt = new Date();
    return response.data
      .map(product => this.normalizeProduct(shop, product, observedAt))
      .filter((item): item is ProductObservation => Boolean(item))
      .filter(item => !query.gtin || String(item.gtin ?? "").toLowerCase() === query.gtin.trim().toLowerCase());
  }

  private normalizeProduct(shop: CommerceShop, product: WooProduct, observedAt: Date): ProductObservation | undefined {
    if (!product.name && product.id === undefined) return undefined;
    const amount = priceAmount(product.prices);

    return {
      shopId: shop.id,
      platform: this.platform,
      externalId: product.id !== undefined ? String(product.id) : product.slug,
      sku: product.sku || undefined,
      gtin: product.global_unique_id || undefined,
      title: String(product.name || product.slug || `WooCommerce product ${product.id ?? ""}`).trim(),
      url: product.permalink || undefined,
      variantTitle: product.variation || undefined,
      available: Boolean(product.is_in_stock),
      price: amount !== undefined
        ? { amount, currency: product.prices?.currency_code || undefined }
        : undefined,
      attributes: {
        slug: product.slug,
        type: product.type,
        hasOptions: product.has_options
      },
      observedAt
    };
  }
}
