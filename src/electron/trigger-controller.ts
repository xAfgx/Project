import { createServer, type IncomingMessage } from "http";

export interface TriggerServerOptions {
  port?: number;
  token?: string;
  /** Starts a task by id; returns success plus an optional error. */
  startTask: (taskId: string) => Promise<{ success: boolean; error?: string }>;
  onLog?: (message: string) => void;
}

export interface TriggerServerHandle {
  port: number;
  token: string;
  close: () => void;
}

function readBody(request: IncomingMessage, limit = 64 * 1024): Promise<string> {
  return new Promise((resolve, reject) => {
    let data = "";
    request.on("data", chunk => {
      data += chunk;
      if (data.length > limit) {
        reject(new Error("body too large"));
        request.destroy();
      }
    });
    request.on("end", () => resolve(data));
    request.on("error", reject);
  });
}

/**
 * Localhost-only HTTP trigger so an external always-on listener (e.g. a Discord
 * watcher on a Raspberry Pi) can start a predefined ARES task on a drop.
 * Bound to 127.0.0.1 and protected by a bearer token.
 */
export function registerTriggerServer(options: TriggerServerOptions): TriggerServerHandle {
  const configuredPort = options.port ?? Number(process.env["ARES_TRIGGER_PORT"] ?? 8791);
  const port = Number.isFinite(configuredPort) ? Math.max(1, Math.floor(configuredPort)) : 8791;
  const token = (options.token ?? process.env["ARES_TRIGGER_TOKEN"]?.trim() ?? "").trim();
  const log = options.onLog ?? (() => undefined);

  const server = createServer(async (request, response) => {
    const respond = (status: number, body: Record<string, unknown>): void => {
      response.writeHead(status, { "content-type": "application/json" });
      response.end(JSON.stringify(body));
    };
    try {
      if (request.method === "GET" && request.url === "/health") {
        respond(200, { ok: true, service: "ares-trigger" });
        return;
      }
      if (request.method !== "POST" || !request.url?.startsWith("/trigger")) {
        respond(404, { ok: false, error: "not found" });
        return;
      }
      if (token) {
        const auth = String(request.headers["authorization"] ?? "");
        if (auth !== `Bearer ${token}`) {
          respond(401, { ok: false, error: "unauthorized" });
          return;
        }
      }
      const raw = await readBody(request);
      const payload = raw ? JSON.parse(raw) as Record<string, unknown> : {};
      const taskId = String(payload["taskId"] ?? payload["task"] ?? "").trim();
      if (!taskId) {
        respond(400, { ok: false, error: "taskId missing" });
        return;
      }
      log(`trigger task=${taskId} from=${String(request.headers["user-agent"] ?? "?")}`);
      const result = await options.startTask(taskId);
      respond(result.success ? 200 : 409, { ok: result.success, taskId, error: result.error });
    } catch (error) {
      respond(500, { ok: false, error: error instanceof Error ? error.message : String(error) });
    }
  });

  server.on("error", error => log(`trigger server error: ${error instanceof Error ? error.message : String(error)}`));
  server.listen(port, "127.0.0.1", () => log(`trigger endpoint http://127.0.0.1:${port}/trigger token=${token ? "set" : "none"}`));

  return { port, token, close: () => server.close() };
}
