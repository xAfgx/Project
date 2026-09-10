import * as fs from "fs";
import * as os from "os";
import * as path from "path";
import { deleteRegisteredProfileCookieSnapshots } from "../cookies/profile-cookie-snapshot-registry";
import { BrowserProfileInUseError } from "./errors";

const PROFILE_LOCK_FILE = ".ares-profile.lock";

interface BrowserProfileLockRecord {
  ownerId: string;
  pid: number;
  acquiredAt: string;
}

export interface BrowserProfileLease {
  ownerId: string;
  pid: number;
  userDataDir: string;
  lockPath: string;
  acquiredAt: string;
  release(): void;
}

export function safeProfilePartitionName(profileId: string): string {
  const normalized = String(profileId ?? "").trim().replace(/[^a-zA-Z0-9_-]/g, "_");
  if (!normalized) throw new TypeError("profileId must not be empty.");
  return `profile_${normalized}`;
}

export function resolveBrowserProfileRoot(configuredRoot?: string): string {
  const value = configuredRoot?.trim() || process.env["ARES_BROWSER_PROFILE_ROOT"]?.trim();
  return value || path.join(os.tmpdir(), "ares-browser-profiles");
}

export function resolveProfileUserDataDir(profileId: string, configuredRoot?: string): string {
  return path.join(resolveBrowserProfileRoot(configuredRoot), safeProfilePartitionName(profileId));
}

function lockPathFor(userDataDir: string): string {
  return path.join(path.resolve(userDataDir), PROFILE_LOCK_FILE);
}

function isProcessAlive(pid: number): boolean {
  if (!Number.isInteger(pid) || pid <= 0) return false;
  try { process.kill(pid, 0); return true; }
  catch { return false; }
}

function readLock(lockPath: string): BrowserProfileLockRecord | undefined {
  try {
    const parsed = JSON.parse(fs.readFileSync(lockPath, "utf8")) as Partial<BrowserProfileLockRecord>;
    if (!parsed.ownerId || !parsed.pid || !parsed.acquiredAt) return undefined;
    return { ownerId: String(parsed.ownerId), pid: Number(parsed.pid), acquiredAt: String(parsed.acquiredAt) };
  } catch { return undefined; }
}

function removeStaleLock(lockPath: string): boolean {
  const current = readLock(lockPath);
  if (current && isProcessAlive(current.pid)) return false;
  try { fs.unlinkSync(lockPath); return true; }
  catch (error) { return (error as NodeJS.ErrnoException).code === "ENOENT"; }
}

export function inspectBrowserProfileLease(userDataDir: string): BrowserProfileLockRecord | undefined {
  const lockPath = lockPathFor(userDataDir);
  if (!fs.existsSync(lockPath)) return undefined;
  const current = readLock(lockPath);
  if (!current || !isProcessAlive(current.pid)) {
    removeStaleLock(lockPath);
    return undefined;
  }
  return current;
}

export function acquireBrowserProfileLease(userDataDir: string, ownerId: string): BrowserProfileLease {
  const normalizedOwner = String(ownerId ?? "").trim();
  if (!normalizedOwner) throw new TypeError("ownerId must not be empty.");

  const normalizedDir = path.resolve(userDataDir);
  fs.mkdirSync(normalizedDir, { recursive: true });
  const lockPath = lockPathFor(normalizedDir);

  for (let attempt = 0; attempt < 2; attempt++) {
    const acquiredAt = new Date().toISOString();
    const record: BrowserProfileLockRecord = { ownerId: normalizedOwner, pid: process.pid, acquiredAt };

    try {
      const fd = fs.openSync(lockPath, "wx");
      try { fs.writeFileSync(fd, JSON.stringify(record), "utf8"); }
      finally { fs.closeSync(fd); }

      let released = false;
      return {
        ownerId: normalizedOwner,
        pid: process.pid,
        userDataDir: normalizedDir,
        lockPath,
        acquiredAt,
        release: () => {
          if (released) return;
          released = true;
          const current = readLock(lockPath);
          if (current && current.ownerId === normalizedOwner && current.pid === process.pid) {
            try { fs.unlinkSync(lockPath); }
            catch (error) { if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error; }
          }
        }
      };
    } catch (error) {
      const code = (error as NodeJS.ErrnoException).code;
      if (code !== "EEXIST") throw error;
      if (attempt === 0 && removeStaleLock(lockPath)) continue;
      const current = inspectBrowserProfileLease(normalizedDir);
      throw new BrowserProfileInUseError(normalizedDir, current?.ownerId);
    }
  }

  throw new BrowserProfileInUseError(normalizedDir);
}

export function removeProfileUserDataDir(profileId: string, configuredRoot?: string): void {
  const userDataDir = resolveProfileUserDataDir(profileId, configuredRoot);
  const exists = fs.existsSync(userDataDir);
  if (exists) {
    const lease = acquireBrowserProfileLease(userDataDir, `delete:${profileId}`);
    try { fs.rmSync(userDataDir, { recursive: true, force: true }); }
    finally { lease.release(); }
  }
  deleteRegisteredProfileCookieSnapshots(profileId);
}
