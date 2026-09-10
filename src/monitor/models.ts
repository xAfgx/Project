import type { CommercePlatform } from "../commerce/platforms";

export interface ProductQuery {
  searchTerm?: string;
  sku?: string;
  gtin?: string;
  url?: string;
}

export interface ProductCriteria extends ProductQuery {
  requireAvailable?: boolean;
  minStock?: number;
  minPrice?: number;
  maxPrice?: number;
  minimumScore?: number;
}

export interface ProductPrice {
  amount: number;
  currency?: string;
}

export interface ProductObservation {
  shopId: string;
  platform: CommercePlatform;
  externalId?: string;
  sku?: string;
  gtin?: string;
  title: string;
  url?: string;
  variantId?: string;
  variantTitle?: string;
  available: boolean;
  stock?: number;
  price?: ProductPrice;
  attributes?: Record<string, string | number | boolean | null | undefined>;
  observedAt: Date;
}

export interface ProductMatchResult {
  matched: boolean;
  score: number;
  matchedTokens: string[];
  missingTokens: string[];
  reasons: string[];
}

export type ProductChangeType =
  | "first-seen"
  | "availability-changed"
  | "stock-increased"
  | "stock-decreased"
  | "price-changed"
  | "content-changed"
  | "unchanged";

export interface ProductMonitorEvent {
  key: string;
  type: ProductChangeType;
  current: ProductObservation;
  previous?: ProductObservation;
  match: ProductMatchResult;
  observedAt: Date;
}
