import {
  ITaskExecutor,
  IBrowserManager,
  IProxyManager,
  IShopAdapter
} from "../interfaces";
import { CancellationManager } from "../cancellation-manager";
import { EventBus } from "../event-bus";
import { Task } from "../models";

export class TaskExecutor implements ITaskExecutor {
  constructor(
    private readonly eventBus: EventBus,
    private readonly cancellation: CancellationManager,
    private readonly browser: IBrowserManager,
    private readonly shop: IShopAdapter,
    private readonly proxy: IProxyManager
  ) {}

  async execute(task: Task): Promise<boolean> {
    const signal = this.cancellation.createCancellation(task.id);
    let proxyId: string | undefined;

    try {
      await this.browser.launchBrowser();
      if (signal.aborted) return false;

      proxyId = await this.proxy.getProxy();
      if (signal.aborted) return false;

      if (!(await this.shop.findProduct(task)) || signal.aborted) return false;

      task.state = "PRODUCT_FOUND" as Task["state"];
      this.eventBus.emit("taskUpdated", task);

      if (!(await this.shop.addToCart(task)) || signal.aborted) return false;

      task.state = "CART" as Task["state"];
      this.eventBus.emit("taskUpdated", task);

      if (!(await this.shop.checkout(task)) || signal.aborted) return false;

      return true;
    } catch (error) {
      task.lastError = error instanceof Error ? error.message : String(error);
      return false;
    } finally {
      await this.browser.closeBrowser().catch(() => undefined);
      if (proxyId) this.proxy.releaseProxy(proxyId);
      this.cancellation.cancelTask(task.id);
    }
  }
}
