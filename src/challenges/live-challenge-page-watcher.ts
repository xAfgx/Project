import type { Page, Frame } from "../browser-worker/types";
import { LiveChallengeHandler } from "./live-challenge-handler";

const activePages = new WeakSet<Page>();
const defaultHandler = new LiveChallengeHandler();

export function attachLiveChallengePageWatcher(page: Page): () => void {
  let isChecking = false;

  const runCheck = async (_eventName: string) => {
    if (page.isClosed() || isChecking || activePages.has(page)) return;

    isChecking = true;
    activePages.add(page);

    try {
      await page.waitForTimeout(600);
      if (page.isClosed()) return;

      await defaultHandler.handleLiveChallenge(page);
    } catch {
      // Navigationsfehler ignorieren
    } finally {
      isChecking = false;
      activePages.delete(page);
    }
  };

  const onLoad = () => void runCheck("load");
  const onFrame = (frame: Frame) => {
    if (frame === page.mainFrame()) void runCheck("framenavigated");
  };

  page.on("load", onLoad);
  page.on("framenavigated", onFrame);

  void runCheck("initial");

  return () => {
    page.off("load", onLoad);
    page.off("framenavigated", onFrame);
    activePages.delete(page);
  };
}