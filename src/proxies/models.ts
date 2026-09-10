export type ProxyProtocol = "http" | "https" | "socks5";

export interface ProxyReputation {
  source: "proxycheck.io";
  available: boolean;
  riskScore?: number;
  riskLevel?: "low" | "high" | "critical";
  attackTotal?: number;
  attackHistory?: Record<string, number>;
  spamHits?: number;
  proxyDetected?: boolean;
  detectedType?: string;
  lastSeen?: string;
  error?: string;
}

export interface ProxyGeo {
  country?: string;
  countryCode?: string;
  region?: string;
  city?: string;
  provider?: string;
  asn?: string;
  latitude?: number;
  longitude?: number;
}

export interface ProxyHealthResult {
  proxyId: string;
  status: "online" | "offline";
  checkedAt: string;
  latencyMs?: number;
  exitIp?: string;
  geo?: ProxyGeo;
  reputation?: ProxyReputation;
  error?: string;
}

export interface AresProxy {
  id: string;
  name: string;
  protocol: ProxyProtocol;
  host: string;
  port: number;
  username?: string;
  password?: string;
  health?: ProxyHealthResult;
}

export interface ProxySelection {
  mode: "profile-default" | "direct" | "proxy";
  proxyId?: string;
}
