import type { CommercePlatform, CommerceShop } from "../platforms";
import type { ProductObservation, ProductQuery } from "../../monitor/models";

export interface ProductApiProbeResult {
  platform: CommercePlatform;
  endpoint: string;
  reachable: boolean;
  status?: number;
  publicReadable: boolean;
  reason?: string;
}

export interface CommerceProductApiAdapter {
  readonly platform: CommercePlatform;
  probe(shop: CommerceShop): Promise<ProductApiProbeResult>;
  search(shop: CommerceShop, query: ProductQuery, limit?: number): Promise<ProductObservation[]>;
}

export interface JsonHttpResponse<T> {
  status: number;
  headers: Record<string, string>;
  data?: T;
  text?: string;
}

export interface JsonHttpClient {
  get<T>(url: string, headers?: Record<string, string>): Promise<JsonHttpResponse<T>>;
}

export interface TextHttpResponse {
  status: number;
  headers: Record<string, string>;
  text: string;
  url: string;
}

export interface TextHttpClient {
  get(url: string, headers?: Record<string, string>): Promise<TextHttpResponse>;
}
