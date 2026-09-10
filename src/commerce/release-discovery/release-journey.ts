import type { Page } from "../../browser-worker/types";
import type { CommerceShop } from "../platforms";
import type { ProductObservation } from "../../monitor/models";

export interface ReleaseDiscoveryInput {
  productName: string;
  keywords: string[];
}

export interface ReleaseJourney {
  supports(shop: CommerceShop): boolean;
  discover(page: Page, shop: CommerceShop, input: ReleaseDiscoveryInput): Promise<ProductObservation | undefined>;
  addToCart(page: Page, shop: CommerceShop, product: ProductObservation): Promise<void>;
  openCheckout(page: Page, shop: CommerceShop): Promise<void>;
  isReadyForFinalSubmit(page: Page, shop: CommerceShop): Promise<boolean>;
  advanceCheckout(page: Page, shop: CommerceShop): Promise<boolean>;
  /** Returns only whether the guarded irreversible submit click was dispatched. */
  submitOrder(page: Page, shop: CommerceShop, allowFinalPurchase: () => boolean): Promise<boolean>;
  /** Optional strong post-submit confirmation. Missing implementations fail closed. */
  isOrderConfirmed?(page: Page, shop: CommerceShop): Promise<boolean>;
}
