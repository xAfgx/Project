import { Injectable } from "@angular/core";
import { EN_TRANSLATIONS, DE_TRANSLATIONS } from "./translations";

export type Language = "en" | "de";

const STORAGE_KEY = "ares.uiLanguage";

@Injectable({ providedIn: "root" })
export class I18nService {
  private lang: Language = "en";
  private readonly listeners = new Set<() => void>();

  constructor() {
    this.lang = this.readInitialLanguage();
    this.applyDocumentLanguage();
  }

  get language(): Language {
    return this.lang;
  }

  get languages(): Array<{ id: Language; label: string }> {
    return [
      { id: "en", label: "English" },
      { id: "de", label: "Deutsch" }
    ];
  }

  setLanguage(language: Language): void {
    if (language !== "en" && language !== "de") return;
    if (language === this.lang) return;
    this.lang = language;
    try {
      localStorage.setItem(STORAGE_KEY, language);
    } catch {
      // Local preference only; the UI keeps working without storage.
    }
    this.applyDocumentLanguage();
    for (const listener of [...this.listeners]) listener();
  }

  onLanguageChange(listener: () => void): () => void {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  t(key: string, params?: Record<string, string | number>): string {
    const dictionary = this.lang === "de" ? DE_TRANSLATIONS : EN_TRANSLATIONS;
    let value = dictionary[key] ?? key;
    if (params) {
      for (const [name, replacement] of Object.entries(params)) {
        value = value.split(`{${name}}`).join(String(replacement));
      }
    }
    return value;
  }

  private readInitialLanguage(): Language {
    try {
      const stored = localStorage.getItem(STORAGE_KEY);
      if (stored === "en" || stored === "de") return stored;
    } catch {
      // Ignore unavailable storage.
    }
    if (typeof navigator !== "undefined" && /^de\b/i.test(navigator.language || "")) return "de";
    return "en";
  }

  private applyDocumentLanguage(): void {
    if (typeof document !== "undefined") document.documentElement.lang = this.lang;
  }
}
