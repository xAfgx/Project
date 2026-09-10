"""
ARES WebRTC Proxy Policy Module
================================
WebRTC must be ALLOWED, not blocked. The goal is cross-layer IP consistency:
- HTTP connection shows Residential Proxy IP
- WebRTC connection shows the SAME Residential Proxy IP
- DataDome sees: HTTP=Proxy-IP, WebRTC=Proxy-IP → GREEN LIGHT

Chrome handles the proxy routing via:
- --force-webrtc-ip-handling-policy=disable_non_proxied_udp
- --enable-features=WebRTCNetworkIgnoreNonProxyUDP

This module installs the CDP script that scrubs private/local ICE candidates
while allowing proxy-routed candidates to pass through.
"""

# CDP script that prevents WebRTC from leaking real local IPs
# while keeping WebRTC functional through the Residential Proxy
WEBRTC_PROXY_CDP_SCRIPT = r"""
(function() {
   'use strict';
   if (window.__aresWebRtcProxyInstalled) return;
   window.__aresWebRtcProxyInstalled = true;

   // Private address patterns that could leak real network topology
   var PRIVATE_V4 = /^(?:10\.|127\.|169\.254\.|192\.168\.|172\.(?:1[6-9]|2\d|3[01])\.)/;
   var PRIVATE_V6 = /^(?:::1$|fe80:|fc|fd)/i;

   var isPrivateAddress = function(address) {
      var value = String(address || "").toLowerCase();
      if (!value || value.endsWith(".local")) return true;
      return PRIVATE_V4.test(value) || PRIVATE_V6.test(value);
   };

   // Scrub only private candidates - proxy-routed ones pass through
   var scrubCandidate = function(candidate) {
      if (!candidate) return null;
      var address = candidate.address || "";
      var type = candidate.type || "";
      if (type === "host" && isPrivateAddress(address)) return null;
      return candidate;
   };

   // Wrap RTCPeerConnection to filter leaking candidates
   var OrigRTCPeerConnection = window.RTCPeerConnection;
   if (!OrigRTCPeerConnection) return;

   var AresRTCPeerConnection = function(config) {
      var self = this;
      OrigRTCPeerConnection.call(this, config);
      var origOnIceCandidate = this.onicecandidate;
      var selfRef = this;
      Object.defineProperty(this, 'onicecandidate', {
         get: function() { return origOnIceCandidate; },
         set: function(listener) {
            origOnIceCandidate = listener;
            var wrapped = function(event) {
               var candidate = event.candidate;
               if (candidate && isPrivateAddress(candidate.address || "")) return;
               if (listener) listener.call(self, event);
            };
            OrigRTCPeerConnection.prototype.addEventListener.call(selfRef, "icecandidate", wrapped);
         },
         configurable: true
      });
   };
   AresRTCPeerConnection.prototype = OrigRTCPeerConnection.prototype;
   window.RTCPeerConnection = AresRTCPeerConnection;
})();
"""


def install_webrtc_proxy_policy(context) -> bool:
    """
    Install the WebRTC proxy policy via CDP.
    
    This ensures WebRTC uses the Residential Proxy for all connections
    and scrubs any private/local ICE candidates that could leak the real IP.
    
    Args:
        context: Browser context for CDP script injection
        
    Returns:
        True if successfully installed
    """
    try:
        if not context or not hasattr(context, 'addInitScript'):
            return False
        context.addInitScript({"content": WEBRTC_PROXY_CDP_SCRIPT})
        return True
    except Exception:
        return False


def get_webrtc_proxy_config() -> dict:
    """
    Get the WebRTC proxy configuration for Chrome flags.
    
    Returns:
        Dictionary with Chrome flag configurations for WebRTC proxy routing
    """
    return {
        "force_webrtc_ip_handling_policy": "disable_non_proxied_udp",
        "enable_features": "WebRTCNetworkIgnoreNonProxyUDP",
        "enable_webrtc_ip_handling_policy": True,
        "enforce_webrtc_ip_permission_check": True
    }


if __name__ == "__main__":
    config = get_webrtc_proxy_config()
    print("WebRTC Proxy Policy configured:")
    for key, value in config.items():
        print(f"  {key}: {value}")
    print("\nWebRTC: ALLOWED, proxied through Residential Proxy.")
    print("HTTP-IP = WebRTC-IP = Proxy-IP → DataDome GREEN LIGHT.")
