import { TaskOrchestrator } from "./orchestrator";
import {
  BrowserManagerMock,
  ProxyManagerMock,
  ShopAdapterMock,
  TaskExecutorMock,
  TaskRepositoryMock
} from "./mocks";

const orchestrator = new TaskOrchestrator(
  new TaskRepositoryMock(),
  new TaskExecutorMock(),
  new BrowserManagerMock(),
  new ShopAdapterMock(),
  new ProxyManagerMock()
);

export { orchestrator };