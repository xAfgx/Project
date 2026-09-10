import type { CommercePlatform, CommerceShop } from "../platforms";
import type { ProductObservation, ProductQuery } from "../../monitor/models";
import { GenericHtmlProductApiAdapter } from "./generic-html-product-api-adapter";
import { NodeJsonHttpClient } from "./http-json-client";
import { NodeTextHttpClient } from "./http-text-client";
import { ShopifyProductApiAdapter } from "./shopify-product-api-adapter";
import type {
  CommerceProductApiAdapter,
  JsonHttpClient,
  ProductApiProbeResult,
  TextHttpClient
} from "./types";
import { WooCommerceProductApiAdapter } from "./woocommerce-product-api-adapter";

export class CommerceProductApiRouter {
  private readonly adapters = new Map<CommercePlatform, CommerceProductApiAdapter>();
  private readonly genericHtml: GenericHtmlProductApiAdapter;

  constructor(
    includeBuiltIns = true,
    http: JsonHttpClient = new NodeJsonHttpClient(),
    textHttp: TextHttpClient = new NodeTextHttpClient()
  ) {
    this.genericHtml = new GenericHtmlProductApiAdapter(textHttp);
    if (includeBuiltIns) {
      this.register(new ShopifyProductApiAdapter(http));
      this.register(new WooCommerceProductApiAdapter(http));
    }
  }

  register(adapter: CommerceProductApiAdapter): void {
    this.adapters.set(adapter.platform, adapter);
  }

  get(platform: CommercePlatform): CommerceProductApiAdapter | undefined {
    return this.adapters.get(platform);
  }

  async probe(shop: CommerceShop): Promise<ProductApiProbeResult> {
    const adapter = this.get(shop.platform);
    if (adapter) {
      try {
        const result = await adapter.probe(shop);
        if (result.publicReadable) return result;
      } catch {
        // Public storefront HTML remains a valid fallback even when a platform
        // specific anonymous endpoint is missing or disabled by the merchant.
      }
    }
    return this.genericHtml.probe(shop);
  }

  async search(shop: CommerceShop, query: ProductQuery, limit = 50): Promise<ProductObservation[]> {
    const adapter = this.get(shop.platform);
    if (adapter) {
      try {
        const results = await adapter.search(shop, query, limit);
        if (results.length) return results;
      } catch {
        // Fall through to the public HTML monitor. This deliberately does not
        // attempt credentialed/private platform APIs.
      }
    }
    return this.genericHtml.search(shop, query, limit);
  }

  supportedPlatforms(): CommercePlatform[] {
    return [...this.adapters.keys()];
  }
}
