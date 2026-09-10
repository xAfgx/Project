export type LiveChallengeType =
  | "turnstile"
  | "recaptcha"
  | "hcaptcha"
  | "shopify-checkpoint"
  | "shopify-queue"
  | "generic-interstitial"
  | "queue-it"
  | "datadome"
  | "waiting-room"
  | "unknown";

export interface LiveChallengeDetection {
  detected: boolean;
  type?: LiveChallengeType;
  url: string;
  title?: string;
  details?: string;
}

export interface LiveChallengeOptions {
  timeoutMs?: number;
  pollIntervalMs?: number;
  autoSolveTurnstile?: boolean;
  bringToFrontOnChallenge?: boolean;
  onStatusChange?: (status: string, detection?: LiveChallengeDetection) => void;
}

export interface LiveChallengeResult {
  handled: boolean;
  type?: LiveChallengeType;
  resolved: boolean;
  durationMs: number;
  error?: string;
}
