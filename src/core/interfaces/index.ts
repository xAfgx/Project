import { Task, Shop, Product, Session, Proxy, BrowserConfig } from '../models';

export interface ITaskRepository {
  save(task: Task): Promise<void>;
  findById(id: string): Promise<Task | null>;
  findAll(): Promise<Task[]>;
  update(task: Task): Promise<void>;
  delete(id: string): Promise<void>;
}

export interface IShopRepository {
  save(shop: Shop): Promise<void>;
  findById(id: string): Promise<Shop | null>;
  findAll(): Promise<Shop[]>;
  update(shop: Shop): Promise<void>;
  delete(id: string): Promise<void>;
}

export interface IProductRepository {
  save(product: Product): Promise<void>;
  findById(id: string): Promise<Product | null>;
  findAll(): Promise<Product[]>;
  update(product: Product): Promise<void>;
  delete(id: string): Promise<void>;
}

export interface ISessionRepository {
  save(session: Session): Promise<void>;
  findById(id: string): Promise<Session | null>;
  findAll(): Promise<Session[]>;
  update(session: Session): Promise<void>;
  delete(id: string): Promise<void>;
}

export interface IProxyRepository {
  save(proxy: Proxy): Promise<void>;
  findById(id: string): Promise<Proxy | null>;
  findAll(): Promise<Proxy[]>;
  update(proxy: Proxy): Promise<void>;
  delete(id: string): Promise<void>;
}

// Browser-Abstraktionen (nur Interfaces)
export interface IBrowserService {
  launch(config: BrowserConfig): Promise<void>;
  close(): Promise<void>;
  navigate(url: string): Promise<void>;
  waitForSelector(selector: string, timeout?: number): Promise<void>;
  click(selector: string): Promise<void>;
  fill(selector: string, value: string): Promise<void>;
  getHtml(): Promise<string>;
  getTitle(): Promise<string>;
  getCurrentUrl(): Promise<string>;
  takeScreenshot(path?: string): Promise<void>;
  executeScript(script: string): Promise<any>;
  waitForNetworkIdle(timeout?: number): Promise<void>;
  setCookie(cookie: any): Promise<void>;
  getCookies(): Promise<any[]>;
}

export interface IChallengeSolver {
  solveChallenge(challenge: any): Promise<string>;
  verifySolution(solution: string, challenge: any): Promise<boolean>;
}
