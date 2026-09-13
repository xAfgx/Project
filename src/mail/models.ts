/**
 * Generic IMAP mailbox configuration. Nothing here is provider specific; the
 * user supplies host/port/TLS and credentials for whatever mailbox they use.
 */
export interface ImapMailboxConfig {
  id: string;
  name: string;
  host: string;
  port: number;
  secure: boolean;
  user: string;
  password: string;
  /** IMAP folder; defaults to INBOX. */
  mailbox?: string;
}

export interface MailMessage {
  uid: number;
  subject: string;
  from: string;
  to: string[];
  /** ISO timestamp, empty when the server did not report a date. */
  date: string;
  text: string;
  html: string;
  /** Absolute http(s) links extracted from text and html bodies. */
  links: string[];
}

export interface MailSearchOptions {
  /** Substring match against the From header. */
  from?: string;
  /** Substring match against the To header. */
  to?: string;
  /** Substring match against the subject. */
  subjectContains?: string;
  /** Only messages received on/after this time. */
  since?: Date;
  /** Restrict to messages not yet marked as read. */
  unseenOnly?: boolean;
  /** Maximum number of messages to return (newest first). */
  limit?: number;
}

export interface MailConnectionResult {
  ok: boolean;
  error?: string;
  /** Number of messages currently in the mailbox when the connection worked. */
  messages?: number;
}
