import * as fs from "fs";
import * as path from "path";
import { AresProfile } from "./models";

export class ProfileRepository {
  private readonly profiles = new Map<string, AresProfile>();
  private storagePath?: string;

  constructor(storagePath?: string) {
    const initialPath = storagePath || process.env["ARES_PROFILES_FILE"];
    if (initialPath) {
      this.setStoragePath(initialPath);
    }
  }

  setStoragePath(filePath: string): void {
    this.storagePath = filePath;
    this.loadFromDisk();
  }

  private loadFromDisk(): void {
    if (!this.storagePath) return;
    try {
      if (fs.existsSync(this.storagePath)) {
        const data = fs.readFileSync(this.storagePath, "utf8");
        const list = JSON.parse(data);
        if (Array.isArray(list)) {
          for (const item of list) {
            if (item && item.id) {
              this.profiles.set(item.id, item as AresProfile);
            }
          }
        }
      }
    } catch {
      // Gracefully ignore corrupt storage file
    }
  }

  private persistToDisk(): void {
    if (!this.storagePath) return;
    try {
      const dir = path.dirname(this.storagePath);
      if (!fs.existsSync(dir)) {
        fs.mkdirSync(dir, { recursive: true });
      }
      fs.writeFileSync(this.storagePath, JSON.stringify([...this.profiles.values()], null, 2), "utf8");
    } catch {
      // Ignore write errors if disk is not writable
    }
  }

  save(profile: AresProfile): AresProfile {
    this.profiles.set(profile.id, profile);
    this.persistToDisk();
    return profile;
  }

  get(id: string): AresProfile | undefined {
    return this.profiles.get(id);
  }

  getAll(): AresProfile[] {
    return [...this.profiles.values()];
  }

  delete(id: string): boolean {
    const deleted = this.profiles.delete(id);
    if (deleted) {
      this.persistToDisk();
    }
    return deleted;
  }
}
