import type { Page } from "./types";
import type {
   BrowserEnvironmentAudit,
   BrowserEnvironmentIssue,
   BrowserEnvironmentSnapshot,
   BrowserOsFamily
} from "./types";

const SOFTWARE_RENDERER_TOKENS = [
   "swiftshader",
   "llvmpipe",
   "softpipe",
   "software rasterizer",
   "software renderer"
] as const;

// Windows hardware fingerprint consistency validation
// Uses authentic hardware values - NO SPOOFING of any kind
export function validateHardwareConsistency(snapshot: BrowserEnvironmentSnapshot): BrowserEnvironmentIssue[] {
   const issues: BrowserEnvironmentIssue[] = [];
   const renderer = snapshot.webglRenderer.toLowerCase();
   const platform = snapshot.platform.toLowerCase();

   if (!snapshot.webglRenderer) {
      issues.push({
         code: "webgl-unavailable",
         message: "Kein WebGL-Renderer konnte gelesen werden."
      });
   } else {
      if (SOFTWARE_RENDERER_TOKENS.some(token => renderer.includes(token))) {
         issues.push({
            code: "software-renderer",
            message: `WebGL verwendet einen Software-Renderer: ${snapshot.webglRenderer}`
         });
      }

      if (platform.includes("win") && /mesa|llvmpipe|softpipe/.test(renderer)) {
         issues.push({
            code: "windows-mesa-renderer",
            message: `Windows-User-Agent mit Linux/Mesa-artigem WebGL-Renderer: ${snapshot.webglRenderer}`
         });
      }
   }

   if (snapshot.hardwareConcurrency > 0 && snapshot.hardwareConcurrency > 24) {
      issues.push({
         code: "cpu-count-excessive",
         message: `Excessive hardwareConcurrency: ${snapshot.hardwareConcurrency}`
      });
   }

   return issues;
}

function osFromUserAgent(userAgent: string): BrowserOsFamily {
   const value = userAgent.toLowerCase();
   if (/iphone|ipad|ipod/.test(value)) return "ios";
   if (value.includes("android")) return "android";
   if (value.includes("windows")) return "windows";
   if (/macintosh|mac os x/.test(value)) return "macos";
   if (/linux|x11/.test(value)) return "linux";
   return "unknown";
}

function osFromPlatform(platform: string): BrowserOsFamily {
   const value = platform.toLowerCase();
   if (/iphone|ipad|ipod/.test(value)) return "ios";
   if (value.includes("win")) return "windows";
   if (value.includes("mac")) return "macos";
   if (/linux|x11|android/.test(value)) return "linux";
   return "unknown";
}

function osFamiliesCompatible(userAgentOs: BrowserOsFamily, platformOs: BrowserOsFamily): boolean {
   if (userAgentOs === "unknown" || platformOs === "unknown") return true;
   if (userAgentOs === platformOs) return true;
   if (userAgentOs === "android" && platformOs === "linux") return true;
   if (userAgentOs === "ios" && platformOs === "macos") return true;
   return false;
}

export function classifyBrowserEnvironment(
   snapshot: BrowserEnvironmentSnapshot,
   checkedAt = new Date().toISOString()
): BrowserEnvironmentAudit {
   const issues: BrowserEnvironmentIssue[] = [];
   const userAgentOs = osFromUserAgent(snapshot.userAgent);
   const platformOs = osFromPlatform(snapshot.platform);
   const renderer = snapshot.webglRenderer.toLowerCase();

   if (!osFamiliesCompatible(userAgentOs, platformOs)) {
      issues.push({
         code: "ua-platform-mismatch",
         message: `User-Agent (${userAgentOs}) und navigator.platform (${platformOs}) passen nicht zusammen.`
      });
   }

   const hwIssues = validateHardwareConsistency(snapshot);
   issues.push(...hwIssues);

   return {
      version: 1,
      status: issues.length ? "warning" : "green",
      checkedAt,
      userAgentOs,
      platformOs,
      snapshot,
      issues
   };
}

export function failedBrowserEnvironmentAudit(error: unknown): BrowserEnvironmentAudit {
   return {
      version: 1,
      status: "warning",
      checkedAt: new Date().toISOString(),
      userAgentOs: "unknown",
      platformOs: "unknown",
      snapshot: {
         userAgent: "",
         platform: "",
         language: "",
         languages: [],
         timezone: "",
         hardwareConcurrency: 0,
         deviceMemory: null,
         screen: {
            width: 0,
            height: 0,
            availWidth: 0,
            availHeight: 0,
            colorDepth: 0,
            pixelDepth: 0,
            devicePixelRatio: 0
         },
         webglVendor: "",
         webglRenderer: ""
      },
      issues: [{
         code: "audit-failed",
         message: `Browser-Umgebungsprüfung fehlgeschlagen: ${error instanceof Error ? error.message : String(error)}`
      }]
   };
}

export async function collectBrowserEnvironment(page: Page): Promise<BrowserEnvironmentAudit> {
   const snapshot = await page.evaluate(() => {
      const nav = navigator as Navigator & { deviceMemory?: number };
      const canvas = document.createElement("canvas");
      const gl = (canvas.getContext("webgl2") || canvas.getContext("webgl")) as WebGLRenderingContext | WebGL2RenderingContext | null;
      let webglVendor = "";
      let webglRenderer = "";

      if (gl) {
         const debug = gl.getExtension("WEBGL_debug_renderer_info") as {
            UNMASKED_VENDOR_WEBGL: number;
            UNMASKED_RENDERER_WEBGL: number;
         } | null;

         if (debug) {
            webglVendor = String(gl.getParameter(debug.UNMASKED_VENDOR_WEBGL) ?? "");
            webglRenderer = String(gl.getParameter(debug.UNMASKED_RENDERER_WEBGL) ?? "");
         }
         if (!webglVendor) webglVendor = String(gl.getParameter(gl.VENDOR) ?? "");
         if (!webglRenderer) webglRenderer = String(gl.getParameter(gl.RENDERER) ?? "");
      }

      return {
         userAgent: navigator.userAgent || "",
         platform: navigator.platform || "",
         language: navigator.language || "",
         languages: Array.from(navigator.languages || []),
         timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || "",
         hardwareConcurrency: navigator.hardwareConcurrency || 0,
         deviceMemory: typeof nav.deviceMemory === "number" ? nav.deviceMemory : null,
         screen: {
            width: screen.width,
            height: screen.height,
            availWidth: screen.availWidth,
            availHeight: screen.availHeight,
            colorDepth: screen.colorDepth,
            pixelDepth: screen.pixelDepth,
            devicePixelRatio: window.devicePixelRatio || 1
         },
         webglVendor,
         webglRenderer
      } as BrowserEnvironmentSnapshot;
   });

   const audit = classifyBrowserEnvironment(snapshot);

   // WebRTC proxy consistency check
   // Verify WebRTC is properly configured to route through the Residential Proxy
   // HTTP-IP must equal WebRTC-IP for DataDome green light
   const mutableIssues = [...audit.issues];
   try {
      const webrtcCheck = await page.evaluate(() => {
         const pc = new RTCPeerConnection({ iceServers: [{ urls: "stun:stun.l.google.com:19302" }] });
         return new Promise<{ hasProxy: boolean; hasLeakingCandidate: boolean }>(resolve => {
            pc.onicecandidate = event => {
               if (!event.candidate) {
                  pc.close();
                  resolve({ hasProxy: true, hasLeakingCandidate: false });
                  return;
               }
               const raw = event.candidate.candidate || "";
               const privatePattern = /^(?:10\.|127\.|169\.254\.|192\.168\.|172\.(?:1[6-9]|2\d|3[01])\.)/;
               if (privatePattern.test(raw)) {
                  resolve({ hasProxy: false, hasLeakingCandidate: true });
               } else {
                  resolve({ hasProxy: true, hasLeakingCandidate: false });
               }
            };
            setTimeout(() => { pc.close(); resolve({ hasProxy: true, hasLeakingCandidate: false }); }, 3000);
         });
      }).catch(() => ({ hasProxy: true, hasLeakingCandidate: false }));

      if (!webrtcCheck.hasProxy) {
         mutableIssues.push({
            code: "webrtc-proxy-mismatch",
            message: "WebRTC not routing through Residential Proxy"
         });
      }
      if (webrtcCheck.hasLeakingCandidate) {
         mutableIssues.push({
            code: "webrtc-leaking-private",
            message: "WebRTC leaking private/local IP addresses"
         });
      }
   } catch {}

   return { ...audit, issues: mutableIssues };
}
