declare module "better-sqlite3" {
  namespace Database {
    interface Options {
      readonly?: boolean;
      fileMustExist?: boolean;
      timeout?: number;
      verbose?: (message?: unknown, ...additionalArgs: unknown[]) => void;
    }

    interface RunResult {
      changes: number;
      lastInsertRowid: number | bigint;
    }

    interface Statement {
      run(...params: unknown[]): RunResult;
      get(...params: unknown[]): unknown;
      all(...params: unknown[]): unknown[];
    }
  }

  class Database {
    constructor(filename: string, options?: Database.Options);
    readonly open: boolean;
    readonly readonly: boolean;
    readonly name: string;
    prepare(source: string): Database.Statement;
    transaction<T extends (...args: any[]) => any>(fn: T): T;
    pragma(source: string, options?: { simple?: boolean }): unknown;
    exec(source: string): this;
    close(): this;
  }

  export = Database;
}
