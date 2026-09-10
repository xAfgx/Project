import { ProductMatcher } from "./product-matcher";
import {
  ProductChangeType,
  ProductCriteria,
  ProductMonitorEvent,
  ProductObservation
} from "./models";

function normalizeKeyPart(value: unknown): string {
  return String(value ?? "").trim().toLowerCase().replace(/\s+/g, "-");
}

function cloneObservation(observation: ProductObservation): ProductObservation {
  return {
    ...observation,
    price: observation.price ? { ...observation.price } : undefined,
    attributes: observation.attributes ? { ...observation.attributes } : undefined,
    observedAt: new Date(observation.observedAt)
  };
}

function stableAttributes(attributes: ProductObservation["attributes"]): string {
  if (!attributes) return "";
  return Object.keys(attributes)
    .sort()
    .map(key => `${key}:${String(attributes[key] ?? "")}`)
    .join("|");
}

export class ProductMonitor {
  private readonly lastSeen = new Map<string, ProductObservation>();

  constructor(private readonly matcher = new ProductMatcher()) {}

  observe(observation: ProductObservation, criteria: ProductCriteria = {}): ProductMonitorEvent | undefined {
    const match = this.matcher.match(observation, criteria);
    if (!match.matched) return undefined;

    const key = this.keyFor(observation);
    const previous = this.lastSeen.get(key);
    const current = cloneObservation(observation);
    this.lastSeen.set(key, current);

    return {
      key,
      type: this.detectChange(previous, current),
      current,
      previous: previous ? cloneObservation(previous) : undefined,
      match,
      observedAt: new Date(current.observedAt)
    };
  }

  getLast(key: string): ProductObservation | undefined {
    const value = this.lastSeen.get(key);
    return value ? cloneObservation(value) : undefined;
  }

  reset(key?: string): void {
    if (key) this.lastSeen.delete(key);
    else this.lastSeen.clear();
  }

  keyFor(observation: ProductObservation): string {
    const identity = observation.externalId
      || observation.sku
      || observation.gtin
      || observation.url
      || `${observation.title}|${observation.variantId ?? observation.variantTitle ?? "default"}`;

    return [observation.platform, observation.shopId, identity]
      .map(normalizeKeyPart)
      .join(":");
  }

  private detectChange(previous: ProductObservation | undefined, current: ProductObservation): ProductChangeType {
    if (!previous) return "first-seen";
    if (previous.available !== current.available) return "availability-changed";

    if (typeof previous.stock === "number" && typeof current.stock === "number") {
      if (current.stock > previous.stock) return "stock-increased";
      if (current.stock < previous.stock) return "stock-decreased";
    }

    const previousPrice = previous.price;
    const currentPrice = current.price;
    if (
      previousPrice?.amount !== currentPrice?.amount
      || previousPrice?.currency !== currentPrice?.currency
    ) {
      if (previousPrice || currentPrice) return "price-changed";
    }

    if (
      previous.title !== current.title
      || previous.variantTitle !== current.variantTitle
      || previous.url !== current.url
      || previous.sku !== current.sku
      || previous.gtin !== current.gtin
      || stableAttributes(previous.attributes) !== stableAttributes(current.attributes)
    ) {
      return "content-changed";
    }

    return "unchanged";
  }
}
