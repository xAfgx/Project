export interface FinalSubmitRecoveryOptions {
  attempts?: number;
  delayMs?: number;
  signal?: AbortSignal;
}

export interface FinalSubmitRecoveryResult {
  confirmed: boolean;
  attempts: number;
  maxAttempts: number;
}

/**
 * Read-only recovery after an irreversible submit was already dispatched.
 * It retries confirmation, never the submit itself. At least two observations
 * are made before callers may mark the retry policy blocked.
 */
export async function confirmFinalSubmitWithRetries(
  check: () => Promise<boolean>,
  options: FinalSubmitRecoveryOptions = {}
): Promise<FinalSubmitRecoveryResult> {
  const maxAttempts = Math.min(5, Math.max(2, Math.floor(Number(options.attempts ?? 2) || 2)));
  const delayMs = Math.min(5_000, Math.max(0, Math.floor(Number(options.delayMs ?? 750) || 0)));
  let attempts = 0;

  while (attempts < maxAttempts && !options.signal?.aborted) {
    attempts += 1;
    if (await check().catch(() => false)) {
      return { confirmed: true, attempts, maxAttempts };
    }
    if (attempts < maxAttempts && delayMs > 0 && !options.signal?.aborted) {
      await delay(delayMs, options.signal);
    }
  }

  return { confirmed: false, attempts, maxAttempts };
}

function delay(ms: number, signal?: AbortSignal): Promise<void> {
  if (signal?.aborted || ms <= 0) return Promise.resolve();
  return new Promise(resolve => {
    const timer = setTimeout(done, ms);
    function done(): void {
      clearTimeout(timer);
      signal?.removeEventListener("abort", done);
      resolve();
    }
    signal?.addEventListener("abort", done, { once: true });
  });
}
