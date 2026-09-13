import { spawn, type ChildProcessWithoutNullStreams } from "child_process";
import * as fs from "fs";
import * as path from "path";

export interface GraphqlRequest {
  url: string;
  operation: string;
  hash: string;
  cacheable?: boolean;
  clientName?: string;
  clientVersion?: string;
  cookieFile?: string;
  variables: Record<string, unknown>;
  pwa: Record<string, unknown>;
  timeout?: number;
}

export interface GraphqlReply {
  ok: boolean;
  status?: number;
  data?: Record<string, unknown>;
  error?: string;
}

function resolveHelper(): string {
  const resourcesPath = (process as NodeJS.Process & { resourcesPath?: string }).resourcesPath || "";
  const candidates = [
    process.env["ARES_MM_GRAPHQL_HELPER"]?.trim(),
    path.join(process.cwd(), "python", "seleniumbase_cdp", "mm_graphql.py"),
    path.join(__dirname, "../../../python/seleniumbase_cdp/mm_graphql.py"),
    resourcesPath ? path.join(resourcesPath, "python", "seleniumbase_cdp", "mm_graphql.py") : undefined
  ].filter((value): value is string => Boolean(value));
  const resolved = candidates.find(candidate => fs.existsSync(candidate));
  if (!resolved) throw new Error("mm_graphql.py wurde nicht gefunden.");
  return resolved;
}

/**
 * Manages one long-running curl_cffi helper process. Because the Python side
 * keeps a single `requests.Session`, cookies persist and refresh automatically
 * across requests (no per-request process spawn, no JSON jar round-trip).
 */
export class MmGraphqlClient {
  private child?: ChildProcessWithoutNullStreams;
  private buffer = "";
  private seq = 0;
  private readonly pending = new Map<number, { resolve: (message: Record<string, unknown>) => void; timer: NodeJS.Timeout }>();

  async request(request: GraphqlRequest, timeoutMs = 45_000): Promise<GraphqlReply> {
    const message = await this.send({ ...request }, timeoutMs);
    return {
      ok: message["ok"] === true,
      status: Number(message["status"] ?? 0) || undefined,
      data: (message["data"] as Record<string, unknown> | undefined),
      error: message["error"] ? String(message["error"]) : undefined
    };
  }

  /** Returns the helper session's current cookie jar (all domains). */
  async cookies(cookieFile?: string, timeoutMs = 15_000): Promise<Array<Record<string, unknown>>> {
    const message = await this.send({ type: "cookies", cookieFile }, timeoutMs);
    return Array.isArray(message["cookies"]) ? message["cookies"] as Array<Record<string, unknown>> : [];
  }

  private send(payload: Record<string, unknown>, timeoutMs: number): Promise<Record<string, unknown>> {
    return new Promise<Record<string, unknown>>(resolve => {
      let child: ChildProcessWithoutNullStreams;
      try {
        child = this.ensure();
      } catch (error) {
        resolve({ ok: false, error: error instanceof Error ? error.message : String(error) });
        return;
      }
      const id = ++this.seq;
      const timer = setTimeout(() => {
        this.pending.delete(id);
        resolve({ ok: false, error: "GraphQL-Helfer Timeout" });
      }, Math.max(1_000, timeoutMs));
      this.pending.set(id, { resolve, timer });
      try {
        child.stdin.write(`${JSON.stringify({ id, ...payload })}\n`);
      } catch (error) {
        clearTimeout(timer);
        this.pending.delete(id);
        resolve({ ok: false, error: error instanceof Error ? error.message : String(error) });
      }
    });
  }

  close(): void {
    const child = this.child;
    this.child = undefined;
    this.failAll("helper closed");
    if (!child || child.exitCode != null) return;
    try {
      child.stdin.write(`${JSON.stringify({ type: "close" })}\n`);
      child.kill();
    } catch {
      // ignore
    }
  }

  private ensure(): ChildProcessWithoutNullStreams {
    if (this.child && this.child.exitCode == null) return this.child;
    const script = resolveHelper();
    const child = spawn(process.env["ARES_PYTHON_EXECUTABLE"]?.trim() || "python", ["-u", script], {
      windowsHide: true,
      env: { ...process.env, PYTHONUNBUFFERED: "1", PYTHONUTF8: "1", PYTHONIOENCODING: "utf-8" }
    });
    this.child = child;
    child.stdout.setEncoding("utf8");
    child.stdout.on("data", chunk => this.consume(String(chunk)));
    child.on("close", () => { if (this.child === child) this.failAll("GraphQL-Helfer beendet"); });
    child.on("error", error => { if (this.child === child) this.failAll(error.message); });
    return child;
  }

  private consume(chunk: string): void {
    this.buffer += chunk;
    const lines = this.buffer.split(/\r?\n/);
    this.buffer = lines.pop() ?? "";
    for (const line of lines) {
      const trimmed = line.trim();
      if (!trimmed) continue;
      let message: Record<string, unknown>;
      try {
        message = JSON.parse(trimmed) as Record<string, unknown>;
      } catch {
        continue;
      }
      if (message["type"] === "ready") continue;
      const id = Number(message["id"]);
      const pending = this.pending.get(id);
      if (!pending) continue;
      this.pending.delete(id);
      clearTimeout(pending.timer);
      pending.resolve(message);
    }
  }

  private failAll(reason: string): void {
    for (const pending of this.pending.values()) {
      clearTimeout(pending.timer);
      pending.resolve({ ok: false, error: reason });
    }
    this.pending.clear();
    this.child = undefined;
  }
}
