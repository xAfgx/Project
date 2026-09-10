import type { CommerceShop } from "../commerce/platforms";
import type { Task } from "../models";
import type { ProductObservation, ProductQuery } from "./models";

export interface BrowserProductFallback {
  search(
    task: Task,
    shop: CommerceShop,
    query: ProductQuery,
    limit?: number,
    signal?: AbortSignal
  ): Promise<ProductObservation[]>;
  cancelTask(taskId: string): Promise<void>;
  close(): Promise<void>;
}
