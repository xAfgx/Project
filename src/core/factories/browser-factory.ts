import { IBrowserService } from '../interfaces';
import { BrowserConfig } from '../models';

export class BrowserFactory {
  static createBrowser(config: BrowserConfig): IBrowserService {
    // Diese Legacy-Core-Factory bleibt für Mock-/Kompatibilitätstests bestehen;
    // der aktive Task-Browserpfad wird separat durch SeleniumBase CDP bereitgestellt.
    return new MockBrowserService(config);
  }
}

// Mock-Implementierung für Testzwecke
class MockBrowserService implements IBrowserService {
  constructor(private config: BrowserConfig) {}

  async launch(): Promise<void> {
    console.log('Launching browser with config:', this.config);
  }

  async close(): Promise<void> {
    console.log('Closing browser');
  }

  async navigate(url: string): Promise<void> {
    console.log(`Navigating to ${url}`);
  }

  async waitForSelector(selector: string, timeout?: number): Promise<void> {
    console.log(`Waiting for selector ${selector} with timeout ${timeout}`);
  }

  async click(selector: string): Promise<void> {
    console.log(`Clicking on ${selector}`);
  }

  async fill(selector: string, value: string): Promise<void> {
    console.log(`Filling ${selector} with ${value}`);
  }

  async getHtml(): Promise<string> {
    return '<html></html>';
  }

  async getTitle(): Promise<string> {
    return 'Mock Title';
  }

  async getCurrentUrl(): Promise<string> {
    return 'https://mock.example.com';
  }

  async takeScreenshot(path?: string): Promise<void> {
    console.log(`Taking screenshot at ${path}`);
  }

  async executeScript(script: string): Promise<any> {
    console.log(`Executing script: ${script}`);
    return {};
  }

  async waitForNetworkIdle(timeout?: number): Promise<void> {
    console.log(`Waiting for network idle with timeout ${timeout}`);
  }

  async setCookie(cookie: any): Promise<void> {
    console.log('Setting cookie:', cookie);
  }

  async getCookies(): Promise<any[]> {
    return [];
  }
}
