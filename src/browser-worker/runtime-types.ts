import type { CommerceShop } from "../commerce/platforms";

export type RuntimeShop = CommerceShop;

export interface ShopifyRuntimeShop extends CommerceShop {
  platform: "shopify";
}

export function isShopifyRuntimeShop(shop: CommerceShop): shop is ShopifyRuntimeShop {
  return shop.platform === "shopify";
}
