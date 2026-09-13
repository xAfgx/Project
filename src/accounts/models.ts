/**
 * A shop account created (or adopted) by the bot. The password is stored
 * OS-encrypted; only the status and the address are shown in the UI.
 */
export type AccountStatus = "pending" | "created" | "confirmed" | "failed";

export interface AccountRecord {
  id: string;
  /** Commerce shop id this account belongs to. */
  shopId: string;
  label: string;
  email: string;
  password: string;
  status: AccountStatus;
  /** Human readable detail for the last status change (error or confirmation). */
  message?: string;
  createdAt: string;
  updatedAt: string;
}

/** Public shape for the UI: password is included only when explicitly requested. */
export interface AccountSummary {
  id: string;
  shopId: string;
  label: string;
  email: string;
  status: AccountStatus;
  message?: string;
  createdAt: string;
  updatedAt: string;
  hasPassword: boolean;
}

export interface CreateAccountInput {
  shopId: string;
  /** Local part or full address to register. May contain {rand} placeholder. */
  email: string;
  /** Optional explicit password; when empty a strong one is generated. */
  password?: string;
  label?: string;
}
