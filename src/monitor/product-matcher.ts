import { ProductCriteria, ProductMatchResult, ProductObservation } from "./models";

function normalize(value: unknown): string {
  return String(value ?? "")
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLowerCase()
    .replace(/[^a-z0-9äöüß]+/gi, " ")
    .replace(/\s+/g, " ")
    .trim();
}

function tokens(value: unknown): string[] {
  return [...new Set(normalize(value).split(" ").filter(token => token.length >= 2))];
}

function observationText(observation: ProductObservation): string {
  const attributes = Object.entries(observation.attributes ?? {})
    .map(([key, value]) => `${key} ${String(value ?? "")}`)
    .join(" ");

  return normalize([
    observation.title,
    observation.variantTitle,
    observation.externalId,
    observation.sku,
    observation.gtin,
    observation.url,
    attributes
  ].join(" "));
}

export class ProductMatcher {
  match(observation: ProductObservation, criteria: ProductCriteria): ProductMatchResult {
    const reasons: string[] = [];
    const matchedTokens: string[] = [];
    const missingTokens: string[] = [];
    const text = observationText(observation);

    if (criteria.sku && normalize(observation.sku) !== normalize(criteria.sku)) {
      return { matched: false, score: 0, matchedTokens, missingTokens, reasons: ["SKU stimmt nicht überein."] };
    }

    if (criteria.gtin && normalize(observation.gtin) !== normalize(criteria.gtin)) {
      return { matched: false, score: 0, matchedTokens, missingTokens, reasons: ["GTIN/EAN stimmt nicht überein."] };
    }

    if (criteria.url && observation.url) {
      const requestedUrl = normalize(criteria.url);
      const observedUrl = normalize(observation.url);
      if (requestedUrl && requestedUrl !== observedUrl && !observedUrl.includes(requestedUrl)) {
        reasons.push("Produkt-URL weicht ab.");
      }
    }

    if (criteria.requireAvailable === true && !observation.available) {
      return { matched: false, score: 0, matchedTokens, missingTokens, reasons: ["Produkt ist nicht verfügbar."] };
    }

    if (typeof criteria.minStock === "number" && (observation.stock ?? 0) < criteria.minStock) {
      return { matched: false, score: 0, matchedTokens, missingTokens, reasons: [`Bestand liegt unter ${criteria.minStock}.`] };
    }

    if (typeof criteria.minPrice === "number" && observation.price && observation.price.amount < criteria.minPrice) {
      return { matched: false, score: 0, matchedTokens, missingTokens, reasons: [`Preis liegt unter ${criteria.minPrice}.`] };
    }

    if (typeof criteria.maxPrice === "number" && observation.price && observation.price.amount > criteria.maxPrice) {
      return { matched: false, score: 0, matchedTokens, missingTokens, reasons: [`Preis liegt über ${criteria.maxPrice}.`] };
    }

    const requestedTokens = tokens(criteria.searchTerm);
    for (const token of requestedTokens) {
      if (text.includes(token)) matchedTokens.push(token);
      else missingTokens.push(token);
    }

    const score = requestedTokens.length
      ? matchedTokens.length / requestedTokens.length
      : 1;
    const minimumScore = Math.min(1, Math.max(0, criteria.minimumScore ?? 0.72));

    if (requestedTokens.length && score < minimumScore) {
      reasons.push(`Keyword-Abdeckung ${Math.round(score * 100)}% liegt unter ${Math.round(minimumScore * 100)}%.`);
    }

    return {
      matched: score >= minimumScore,
      score,
      matchedTokens,
      missingTokens,
      reasons
    };
  }
}
