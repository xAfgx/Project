import type { StoredPaymentPreference } from "../payments/models";

export interface ProxyConfig {
  protocol?: "http" | "https" | "socks5";
  host?: string;
  port?: number;
  username?: string;
  password?: string;
}

export interface AddressProfile {
  /** Combined first address line used by existing profiles and shops. */
  address1: string;
  address2?: string;
  /** Optional structured German-style address parts for shops that split them. */
  street?: string;
  houseNumber?: string;
  postalCode: string;
  city: string;
  countryCode: string;
}

export interface ContactProfile {
  firstName: string;
  lastName: string;
  email: string;
  phone?: string;
  /** "Herr" | "Frau" (or a shop-specific value) for registration forms. */
  salutation?: string;
  /** ISO date (YYYY-MM-DD) used by registration forms that require a birth date. */
  birthDate?: string;
}

export interface BrowserProfileConfig {
  headless?: boolean;
  userAgent?: string;
  /** Enables semantic/KI field resolution. Missing means enabled for backwards compatibility. */
  kiAutofill?: boolean;
}

/** Optional shop account for logged-in checkout. Absence keeps guest checkout. */
export interface AccountCredentials {
  email: string;
  password: string;
}

export interface AresProfile {
  id: string;
  name: string;
  contact: ContactProfile;
  /** Legacy/default address; remains the shipping default for backwards compatibility. */
  address: AddressProfile;
  /** Optional explicit shipping address. Falls back to address when absent. */
  shippingAddress?: AddressProfile;
  /** Optional explicit billing address. Absence means billing should prefer same-as-shipping. */
  billingAddress?: AddressProfile;
  /** Legacy inline proxy config. New UI uses preferredProxyId + Proxy Vault. */
  proxy?: ProxyConfig;
  preferredProxyId?: string;
  browser?: BrowserProfileConfig;
  /** Optional shop account; only used by journeys that support logged-in checkout. */
  account?: AccountCredentials;
  /** Non-sensitive preference only. Card number/CVC live only in the separate encrypted profile payment vault. */
  paymentPreference?: StoredPaymentPreference;
}
