import type { CommercePlatform, CommerceShop } from "./platforms";
import type { ProductObservation, ProductQuery } from "../monitor/models";

export interface ICommerceProductAdapter {
  readonly platform: CommercePlatform;
  observe(shop: CommerceShop, query: ProductQuery): Promise<ProductObservation[]>;
}

export class CommerceProductAdapterRegistry {
  private readonly adapters = new Map<CommercePlatform, ICommerceProductAdapter>();

  register(adapter: ICommerceProductAdapter): void {
    this.adapters.set(adapter.platform, adapter);
  }

  get(platform: CommercePlatform): ICommerceProductAdapter | undefined {
    return this.adapters.get(platform);
  }

  has(platform: CommercePlatform): boolean {
    return this.adapters.has(platform);
  }

  listPlatforms(): CommercePlatform[] {
    return [...this.adapters.keys()];
  }
}
