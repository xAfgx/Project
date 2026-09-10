import type { AddressProfile, AresProfile, ContactProfile } from "../profiles/models";
import type { SemanticFieldValueSource } from "./semantic-target-values";
import type { FieldIntent, SemanticTarget } from "./semantic-target";

export type BillingMappingMode = "prefer-same-as-shipping" | "separate-billing-fields";

export interface SemanticProfileMappingOptions {
  billingMode?: BillingMappingMode;
}

function nonEmpty(value: string | undefined): string | undefined {
  const normalized = value?.trim();
  return normalized ? normalized : undefined;
}

function joinName(contact: ContactProfile): string | undefined {
  return nonEmpty([contact.firstName, contact.lastName].map(value => value.trim()).filter(Boolean).join(" "));
}

function combinedAddress(address: AddressProfile): string | undefined {
  const address1 = nonEmpty(address.address1);
  if (address1) return address1;
  const street = nonEmpty(address.street);
  const houseNumber = nonEmpty(address.houseNumber);
  return nonEmpty([street, houseNumber].filter(Boolean).join(" "));
}

function addressValue(address: AddressProfile, intent: FieldIntent): string | undefined {
  switch (intent) {
    case "street": return nonEmpty(address.street);
    case "houseNumber": return nonEmpty(address.houseNumber);
    case "address1": return combinedAddress(address);
    case "address2": return nonEmpty(address.address2);
    case "city": return nonEmpty(address.city);
    case "postalCode": return nonEmpty(address.postalCode);
    case "countryCode": return nonEmpty(address.countryCode)?.toUpperCase();
    default: return undefined;
  }
}

function contactValue(contact: ContactProfile, intent: FieldIntent): string | undefined {
  switch (intent) {
    case "firstName": return nonEmpty(contact.firstName);
    case "lastName": return nonEmpty(contact.lastName);
    case "fullName": return joinName(contact);
    case "email": return nonEmpty(contact.email);
    case "phone": return nonEmpty(contact.phone);
    default: return undefined;
  }
}

/**
 * Pure domain mapping from SemanticTarget to profile value.
 *
 * - No DOM, selectors, browser APIs or shop knowledge.
 * - unknown context always reads from the explicit default profile address.
 * - billing never collapses into shipping identity; fallback only changes the value source.
 */
export class SemanticProfileMapper implements SemanticFieldValueSource {
  private readonly defaultAddress: AddressProfile;
  private readonly shippingAddress: AddressProfile;
  private readonly explicitBillingAddress?: AddressProfile;
  private readonly billingMode: BillingMappingMode;

  constructor(
    private readonly profile: AresProfile,
    options: SemanticProfileMappingOptions = {}
  ) {
    this.defaultAddress = profile.address;
    this.shippingAddress = profile.shippingAddress ?? this.defaultAddress;
    this.explicitBillingAddress = profile.billingAddress;
    this.billingMode = options.billingMode ?? "prefer-same-as-shipping";
  }

  valueFor(target: SemanticTarget): string | undefined {
    if (target.intent === "unknown") return undefined;

    const contact = contactValue(this.profile.contact, target.intent);
    if (contact) return contact;

    if (target.context === "unknown") {
      return addressValue(this.defaultAddress, target.intent);
    }

    if (target.context === "shipping") {
      return addressValue(this.shippingAddress, target.intent);
    }

    if (this.explicitBillingAddress) {
      return addressValue(this.explicitBillingAddress, target.intent);
    }

    if (this.billingMode === "separate-billing-fields") {
      return addressValue(this.shippingAddress, target.intent)
        ?? addressValue(this.defaultAddress, target.intent);
    }

    // No explicit billing address and same-as-shipping is preferred: billing fields
    // intentionally resolve to missing. A higher checkout layer may choose the
    // same-as-shipping control instead of materializing separate billing fields.
    return undefined;
  }
}
