"""ARES SeleniumBase Pure CDP - Browser Automation Framework."""

from seleniumbase_adapter import SeleniumBaseCdpAdapter
from stealth_protection import StealthProtection, get_stealth_source
from human_behavior import create_human_session, human_delay, think_pause
from cdp_fetch_bridge import CDPFetchBridge, NetworkAnomalyEliminator
from webrtc_proxy_policy import install_webrtc_proxy_policy, get_webrtc_proxy_config

__all__ = [
    "SeleniumBaseCdpAdapter",
    "StealthProtection",
    "get_stealth_source",
    "create_human_session",
    "human_delay",
    "think_pause",
    "CDPFetchBridge",
    "NetworkAnomalyEliminator",
    "install_webrtc_proxy_policy",
    "get_webrtc_proxy_config",
]

__version__ = "2.1.0"
