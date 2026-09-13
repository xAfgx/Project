// Minimal ambient types for imapflow 1.x (2.x ships its own types). Only the
// surface used by src/mail/imap-mailbox.ts is declared.
declare module "imapflow" {
  export interface ImapFlowOptions {
    host: string;
    port: number;
    secure?: boolean;
    auth: { user: string; pass: string };
    logger?: unknown;
    [key: string]: unknown;
  }

  export interface MailboxLock {
    release(): void;
  }

  export interface FetchMessage {
    uid?: number;
    source?: Buffer;
  }

  export class ImapFlow {
    constructor(options: ImapFlowOptions);
    mailbox: unknown;
    connect(): Promise<void>;
    logout(): Promise<void>;
    getMailboxLock(mailbox?: string): Promise<MailboxLock>;
    search(criteria: Record<string, unknown>, options?: { uid?: boolean }): Promise<number[] | false>;
    fetch(range: unknown, options: Record<string, unknown>): AsyncIterable<FetchMessage>;
  }
}
