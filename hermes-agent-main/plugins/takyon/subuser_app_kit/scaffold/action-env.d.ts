// PLATFORM-OWNED action type environment. Keep this an explicit allowlist: adding a broad DOM or
// WebWorker library would advertise browser capabilities that the Deno action process does not own.
import type { SubuserRuntimeClient } from "./_takyon/runtime-client.js";

declare global {
  type TakyonActionContext = SubuserRuntimeClient;
  type TakyonActionPayload = Record<string, unknown>;

  interface TakyonActionHeaders {
    append(name: string, value: string): void;
    delete(name: string): void;
    get(name: string): string | null;
    has(name: string): boolean;
    set(name: string, value: string): void;
  }

  type TakyonActionHeadersInit =
    | TakyonActionHeaders
    | Record<string, string>
    | ReadonlyArray<readonly [string, string]>;

  interface TakyonActionRequestInit {
    method?: string;
    headers?: TakyonActionHeadersInit;
    body?: string | ArrayBuffer | ArrayBufferView | URLSearchParams | null;
    signal?: TakyonActionAbortSignal | null;
  }

  interface TakyonActionAbortSignal {
    readonly aborted: boolean;
    readonly reason: unknown;
    throwIfAborted(): void;
  }

  interface TakyonActionResponse {
    readonly ok: boolean;
    readonly status: number;
    readonly statusText: string;
    readonly url: string;
    readonly headers: TakyonActionHeaders;
    json(): Promise<unknown>;
    text(): Promise<string>;
    arrayBuffer(): Promise<ArrayBuffer>;
  }

  class URLSearchParams implements Iterable<[string, string]> {
    constructor(init?: string | Record<string, string> | ReadonlyArray<readonly [string, string]>);
    append(name: string, value: string): void;
    delete(name: string, value?: string): void;
    entries(): IterableIterator<[string, string]>;
    get(name: string): string | null;
    getAll(name: string): string[];
    has(name: string, value?: string): boolean;
    keys(): IterableIterator<string>;
    set(name: string, value: string): void;
    sort(): void;
    toString(): string;
    values(): IterableIterator<string>;
    [Symbol.iterator](): IterableIterator<[string, string]>;
  }

  class URL {
    constructor(url: string | URL, base?: string | URL);
    hash: string;
    host: string;
    hostname: string;
    href: string;
    origin: string;
    password: string;
    pathname: string;
    port: string;
    protocol: string;
    search: string;
    readonly searchParams: URLSearchParams;
    username: string;
    toString(): string;
    toJSON(): string;
  }

  class Headers implements TakyonActionHeaders {
    constructor(init?: TakyonActionHeadersInit);
    append(name: string, value: string): void;
    delete(name: string): void;
    get(name: string): string | null;
    has(name: string): boolean;
    set(name: string, value: string): void;
  }

  class AbortController {
    readonly signal: TakyonActionAbortSignal;
    abort(reason?: unknown): void;
  }

  class TextEncoder {
    encode(input?: string): Uint8Array;
  }

  class TextDecoder {
    constructor(label?: string, options?: { fatal?: boolean; ignoreBOM?: boolean });
    decode(input?: ArrayBuffer | ArrayBufferView, options?: { stream?: boolean }): string;
  }

  const crypto: {
    randomUUID(): string;
    getRandomValues<T extends ArrayBufferView>(array: T): T;
  };

  const console: {
    debug(...data: unknown[]): void;
    error(...data: unknown[]): void;
    info(...data: unknown[]): void;
    log(...data: unknown[]): void;
    warn(...data: unknown[]): void;
  };

  function fetch(
    input: string | URL,
    init?: TakyonActionRequestInit,
  ): Promise<TakyonActionResponse>;
  function setTimeout(handler: (...args: unknown[]) => void, timeout?: number, ...args: unknown[]): number;
  function clearTimeout(id?: number): void;
  function atob(data: string): string;
  function btoa(data: string): string;
}

export {};
