import type { BrowserContext } from "../types";

/**
 * WebRTC Proxy Consistency Layer
 * ==============================
 * 
 * CRITICAL: WebRTC must be ALLOWED, not blocked. The goal is cross-layer
 * IP consistency:
 * - HTTP connection shows Proxy-IP
 * - WebRTC connection shows the SAME Proxy-IP
 * - DataDome sees: HTTP=Proxy-IP, WebRTC=Proxy-IP → GREEN LIGHT
 * 
 * Chrome handles the proxy routing via:
 * - --force-webrtc-ip-handling-policy=disable_non_proxied_udp
 * - --enable-features=WebRTCNetworkIgnoreNonProxyUDP
 * 
 * This script only scrubs PRIVATE/LOCAL candidates that could leak
 * the real internal network. It does NOT block WebRTC or fabricate
 * ICE addresses. Chromium routes all traffic through the proxy.
 */
export const WEBRTC_PROXY_INIT_SCRIPT = String.raw`(() => {
   // Do NOT disable RTCPeerConnection – WebRTC must remain functional
   // The Chrome policy flags handle proxy routing at the transport level

   const __aresWebRtcProxyPolicyInstalled = true;

   // Private address patterns that could leak real network topology
   const PRIVATE_V4 = /^(?:10\.|127\.|169\.254\.|192\.168\.|172\.(?:1[6-9]|2\d|3[01])\.)/;
   const PRIVATE_V6 = /^(?:::1$|fe80:|fc|fd)/i;

   const candidateAddress = candidate => {
      if (!candidate) return "";
      if (typeof candidate.address === "string" && candidate.address) return candidate.address;
      const raw = String(candidate.candidate || "").trim();
      const parts = raw.split(/\s+/);
      return parts.length > 4 ? parts[4] : "";
   };

   const candidateType = candidate => {
      if (!candidate) return "";
      if (typeof candidate.type === "string" && candidate.type) return candidate.type.toLowerCase();
      const raw = String(candidate.candidate || "");
      const match = raw.match(/\btyp\s+([a-z0-9-]+)/i);
      return match ? match[1].toLowerCase() : "";
   };

   const isPrivateAddress = address => {
      const value = String(address || "").toLowerCase();
      if (!value) return false;
      if (value.endsWith(".local")) return true;
      return PRIVATE_V4.test(value) || PRIVATE_V6.test(value);
   };

   // Only scrub PRIVATE addresses that could leak real network info
   // Host candidates with proxy-routed IPs are ALLOWED through
   const isLeakingCandidate = candidate => {
      const type = candidateType(candidate);
      const address = candidateAddress(candidate);
      // Private/local addresses are leaked – block them
      if (isPrivateAddress(address)) return true;
      // IPv6 link-local is also leaked
      if (address.startsWith("fe80")) return true;
      return false;
   };

   // Scrub only private candidates from SDP
   // Proxy-routed candidates (host type with public IP) remain visible
   const scrubSdp = sdp => String(sdp || "")
      .split(/\r?\n/)
      .filter(line => {
         if (!/^a=candidate:/i.test(line)) return true;
         const raw = line.replace(/^a=/i, "");
         const candidate = { candidate: raw };
         // Filter only leaking candidates
         return !isLeakingCandidate(candidate);
      })
      .join("\r\n");

   const sanitizedDescription = description => {
      if (!description) return description;
      return Object.freeze({ type: description.type, sdp: scrubSdp(description.sdp) });
   };

   // Wrap RTCPeerConnection ONLY to scrub private candidates
   // Do NOT block host candidates – proxy handles routing
   const NativeRTCPeerConnection = window.RTCPeerConnection;
   if (!NativeRTCPeerConnection || window.__aresWebRtcProxyPolicyInstalled) return;

   Object.defineProperty(window, "__aresWebRtcProxyPolicyInstalled", {
      value: true,
      configurable: false,
      enumerable: false,
      writable: false
   });

   class AresRTCPeerConnection extends NativeRTCPeerConnection {
      constructor(configuration) {
         super(configuration);
         this.__aresIceListeners = new Map();
         this.__aresOnIceCandidate = null;

         // Only intercept to scrub leaking candidates
         // Proxy-routed candidates pass through normally
         super.addEventListener("icecandidate", event => {
            if (isLeakingCandidate(event.candidate)) {
               // Scrub private candidate – do not propagate to page
               event.stopImmediatePropagation();
            }
            // All other candidates (including proxy-routed) pass through
         }, true);
      }

      addEventListener(type, listener, options) {
         if (type !== "icecandidate" || !listener) {
            return super.addEventListener(type, listener, options);
         }

         const wrapped = event => {
            // Only filter leaking candidates
            if (isLeakingCandidate(event.candidate)) return;
            if (typeof listener === "function") listener.call(this, event);
            else if (listener && typeof listener.handleEvent === "function") listener.handleEvent(event);
         };
         this.__aresIceListeners.set(listener, wrapped);
         return super.addEventListener(type, wrapped, options);
      }

      removeEventListener(type, listener, options) {
         if (type === "icecandidate" && listener && this.__aresIceListeners.has(listener)) {
            const wrapped = this.__aresIceListeners.get(listener);
            this.__aresIceListeners.delete(listener);
            return super.removeEventListener(type, wrapped, options);
         }
         return super.removeEventListener(type, listener, options);
      }

      set onicecandidate(listener) {
         this.__aresOnIceCandidate = listener;
         super.onicecandidate = listener
            ? event => {
               // Only filter leaking candidates
               if (!isLeakingCandidate(event.candidate)) listener.call(this, event);
            }
            : null;
      }

      get onicecandidate() {
         return this.__aresOnIceCandidate;
      }

      get localDescription() {
         return sanitizedDescription(super.localDescription);
      }

      get currentLocalDescription() {
         return sanitizedDescription(super.currentLocalDescription);
      }

      get pendingLocalDescription() {
         return sanitizedDescription(super.pendingLocalDescription);
      }
   }

   Object.defineProperty(AresRTCPeerConnection, "name", { value: "RTCPeerConnection" });
   Object.setPrototypeOf(AresRTCPeerConnection, NativeRTCPeerConnection);
   window.RTCPeerConnection = AresRTCPeerConnection;
})();`;

export async function installWebRtcProxyPolicy(context: BrowserContext): Promise<void> {
   await context.addInitScript({ content: WEBRTC_PROXY_INIT_SCRIPT });
}

/**
 * Validates that WebRTC proxy policy is correctly configured.
 * Checks that the browser reports consistent IP across HTTP and WebRTC.
 */
export function validateWebRtCProxyConsistency(
   httpIp: string,
   webrtcIp: string
): { consistent: boolean; issues: string[] } {
   const issues: string[] = [];

   if (!httpIp || !webrtcIp) {
      issues.push("Missing IP information for consistency check");
      return { consistent: false, issues };
   }

   if (httpIp !== webrtcIp) {
      issues.push(
         `WebRTC IP mismatch: HTTP=${httpIp}, WebRTC=${webrtcIp}. ` +
         `Both must show the Residential Proxy IP.`
      );
   }

   return { consistent: httpIp === webrtcIp, issues };
}
