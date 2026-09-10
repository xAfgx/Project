export interface TaskConfig {
  id: string;
  name: string;
  shopId?: string;
  maxRetries?: number;
  timeout?: number;
  data?: Record<string, any>;
}

export enum TaskState {
  CREATED = 'CREATED',
  QUEUED = 'QUEUED',
  STARTING = 'STARTING',
  RUNNING = 'RUNNING',
  PAUSED = 'PAUSED',
  PRODUCT_FOUND = 'PRODUCT_FOUND',
  CART = 'CART',
  CHECKOUT = 'CHECKOUT',
  SUCCESS = 'SUCCESS',
  FAILED = 'FAILED',
  CANCELLED = 'CANCELLED',
  RETRYING = 'RETRYING'
}

export interface Task {
  id: string;
  config: TaskConfig;
  state: TaskState;
  createdAt: Date;
  updatedAt: Date;
  lastError?: string;
  retries: number;
  maxRetries: number;
  shopId?: string;
}

export interface Shop {
  id: string;
  name: string;
  url: string;
  userAgent?: string;
  categories?: string[];
  enabled: boolean;
  createdAt: Date;
  updatedAt: Date;
}

export interface ProductCriteria {
  searchTerm?: string;
  minPrice?: number;
  maxPrice?: number;
  brand?: string;
  category?: string;
  inStockOnly?: boolean;
  attributes?: Record<string, any>;
  sortBy?: 'price' | 'rating' | 'relevance';
  sortOrder?: 'asc' | 'desc';
}

export interface Product {
  id: string;
  shopId: string;
  title: string;
  price: number;
  currency: string;
  url: string;
  image?: string;
  inStock: boolean;
  attributes?: Record<string, any>;
  createdAt: Date;
  updatedAt: Date;
}

export interface CartItem {
  id: string;
  productId: string;
  quantity: number;
  price: number;
  currency: string;
  attributes?: Record<string, any>;
  createdAt: Date;
  updatedAt: Date;
}

export interface Cart {
  id: string;
  shopId: string;
  items: CartItem[];
  totalAmount: number;
  currency: string;
  createdAt: Date;
  updatedAt: Date;
}

export interface CheckoutResult {
  success: boolean;
  orderId?: string;
  transactionId?: string;
  paymentMethod?: string;
  shippingAddress?: string;
  billingAddress?: string;
  notes?: string;
  timestamp: Date;
}

export interface Session {
  id: string;
  shopId: string;
  cookies: Cookie[];
  userAgent: string;
  createdAt: Date;
  expiresAt?: Date;
  isActive: boolean;
}

export interface Cookie {
  name: string;
  value: string;
  domain: string;
  path: string;
  secure: boolean;
  httpOnly: boolean;
  sameSite: 'Lax' | 'Strict' | 'None';
  expires?: number;
}

export interface Proxy {
  id: string;
  host: string;
  port: number;
  username?: string;
  password?: string;
  protocol: 'http' | 'https' | 'socks4' | 'socks5';
  enabled: boolean;
  createdAt: Date;
  updatedAt: Date;
}

export interface BrowserConfig {
  headless: boolean;
  userAgent?: string;
  viewport?: { width: number; height: number };
  timeout?: number;
  proxy?: Proxy;
  session?: Session;
  extensions?: string[];
}

export enum ChallengeType {
  CAPTCHA = 'CAPTCHA',
  HONEYPOT = 'HONEYPOT',
  RECAPTCHA = 'RECAPTCHA',
  DYNAMIC_CONTENT = 'DYNAMIC_CONTENT'
}

export interface Challenge {
  id: string;
  taskId: string;
  type: ChallengeType;
  solution?: string;
  solvedAt?: Date;
  createdAt: Date;
  resolved: boolean;
}
