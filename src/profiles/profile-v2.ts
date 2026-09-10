import type { AddressProfile, AresProfile } from "./models";

export interface ProfileV2Draft extends Omit<AresProfile, "address" | "shippingAddress" | "billingAddress"> {
  /** Editable shipping/default address. */
  shippingAddress: AddressProfile;
  /** Editable billing values; ignored while billingSameAsShipping is true. */
  billingAddress: AddressProfile;
  /** Profile/mapping decision, never a DOM-field property. */
  billingSameAsShipping: boolean;
}

export function emptyAddressProfile(countryCode = "DE"): AddressProfile {
  return {
    address1: "",
    address2: "",
    street: "",
    houseNumber: "",
    postalCode: "",
    city: "",
    countryCode
  };
}

function trimmed(value: string | undefined): string {
  return value?.trim() ?? "";
}

export function normalizeAddressProfile(input: AddressProfile): AddressProfile {
  const street = trimmed(input.street);
  const houseNumber = trimmed(input.houseNumber);
  const explicitAddress1 = trimmed(input.address1);
  const address1 = explicitAddress1 || [street, houseNumber].filter(Boolean).join(" ");

  return {
    address1,
    address2: trimmed(input.address2),
    street: street || undefined,
    houseNumber: houseNumber || undefined,
    postalCode: trimmed(input.postalCode),
    city: trimmed(input.city),
    countryCode: trimmed(input.countryCode).toUpperCase() || "DE"
  };
}

function cloneAddress(input: AddressProfile | undefined, fallback?: AddressProfile): AddressProfile {
  const source = input ?? fallback ?? emptyAddressProfile();
  return {
    address1: source.address1 ?? "",
    address2: source.address2 ?? "",
    street: source.street ?? "",
    houseNumber: source.houseNumber ?? "",
    postalCode: source.postalCode ?? "",
    city: source.city ?? "",
    countryCode: source.countryCode || "DE"
  };
}

/**
 * Converts legacy and V2 persisted profiles into one editable representation.
 * Legacy `address` remains the default shipping source when `shippingAddress` is absent.
 */
export function toProfileV2Draft(profile?: AresProfile): ProfileV2Draft {
  if (!profile) {
    return {
      id: "",
      name: "",
      contact: { firstName: "", lastName: "", email: "", phone: "" },
      shippingAddress: emptyAddressProfile(),
      billingAddress: emptyAddressProfile(),
      billingSameAsShipping: true,
      preferredProxyId: "",
      browser: { headless: false, userAgent: "", kiAutofill: true },
      paymentPreference: { method: "card", label: "" }
    };
  }

  const shippingAddress = cloneAddress(profile.shippingAddress, profile.address);
  return {
    id: profile.id,
    name: profile.name,
    contact: { ...profile.contact },
    proxy: profile.proxy ? { ...profile.proxy } : undefined,
    preferredProxyId: profile.preferredProxyId,
    browser: { ...(profile.browser ?? {}), kiAutofill: profile.browser?.kiAutofill !== false },
    paymentPreference: { ...(profile.paymentPreference ?? {}) },
    shippingAddress,
    billingAddress: cloneAddress(profile.billingAddress, shippingAddress),
    billingSameAsShipping: !profile.billingAddress
  };
}

/**
 * Produces the persisted profile contract.
 * `address` is deliberately kept as a copy of shipping/default for legacy consumers.
 */
export function toPersistedAresProfile(draft: ProfileV2Draft): AresProfile {
  const shippingAddress = normalizeAddressProfile(draft.shippingAddress);
  const billingAddress = draft.billingSameAsShipping
    ? undefined
    : normalizeAddressProfile(draft.billingAddress);

  return {
    id: draft.id.trim(),
    name: draft.name.trim(),
    contact: {
      firstName: draft.contact.firstName.trim(),
      lastName: draft.contact.lastName.trim(),
      email: draft.contact.email.trim(),
      phone: trimmed(draft.contact.phone) || undefined
    },
    // Keep the old contract alive while V2 consumers use explicit shippingAddress.
    address: { ...shippingAddress },
    shippingAddress: { ...shippingAddress },
    billingAddress: billingAddress ? { ...billingAddress } : undefined,
    proxy: draft.proxy ? { ...draft.proxy } : undefined,
    preferredProxyId: draft.preferredProxyId || undefined,
    browser: { ...(draft.browser ?? {}), kiAutofill: draft.browser?.kiAutofill !== false },
    paymentPreference: draft.paymentPreference
      ? {
          method: draft.paymentPreference.method,
          label: trimmed(draft.paymentPreference.label) || undefined
        }
      : undefined
  };
}

export function isCompleteCheckoutAddress(address: AddressProfile): boolean {
  const normalized = normalizeAddressProfile(address);
  return Boolean(
    normalized.address1 &&
    normalized.postalCode &&
    normalized.city &&
    normalized.countryCode
  );
}
