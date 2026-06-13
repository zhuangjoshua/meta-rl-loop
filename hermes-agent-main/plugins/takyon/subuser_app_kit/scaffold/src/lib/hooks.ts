import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { client, type TakyonActionError } from "./takyon";

export interface SessionUser {
  [key: string]: unknown;
}

export interface UseSessionResult {
  user: SessionUser | null;
  loading: boolean;
  error: Error | null;
}

/** Loads the current product session once on mount via client.session(). */
export function useSession(): UseSessionResult {
  const [user, setUser] = useState<SessionUser | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<Error | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const payload = await client.session();
        if (cancelled) return;
        const sessionUser =
          payload && typeof payload === "object" && payload.user
            ? (payload.user as SessionUser)
            : null;
        setUser(sessionUser);
        setError(null);
      } catch (err) {
        if (cancelled) return;
        setUser(null);
        setError(err instanceof Error ? err : new Error(String(err)));
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  return { user, loading, error };
}

export interface RecordItem {
  [key: string]: unknown;
}

export interface UseRecordsResult {
  records: RecordItem[];
  loading: boolean;
  error: Error | null;
  refresh: () => Promise<void>;
}

/** Lists records of one type via client.listRecords({ type }); refresh re-lists. */
export function useRecords(type: string): UseRecordsResult {
  const [records, setRecords] = useState<RecordItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<Error | null>(null);
  const aliveRef = useRef(true);

  useEffect(() => {
    aliveRef.current = true;
    return () => {
      aliveRef.current = false;
    };
  }, []);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const payload = await client.listRecords({ type });
      if (!aliveRef.current) return;
      const list = Array.isArray(payload?.records)
        ? (payload.records as RecordItem[])
        : [];
      setRecords(list);
      setError(null);
    } catch (err) {
      if (!aliveRef.current) return;
      setRecords([]);
      setError(err instanceof Error ? err : new Error(String(err)));
    } finally {
      if (aliveRef.current) setLoading(false);
    }
  }, [type]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  return { records, loading, error, refresh };
}

export interface UseActionRunnerResult {
  /** Runs the action; resolves with the result payload, or null when it failed
   *  (the classified error lands in `error`, preserving .kind/.checkoutUrl). */
  run: (payload?: Record<string, unknown>) => Promise<unknown | null>;
  pending: boolean;
  error: TakyonActionError | null;
}

/** Wraps client.createActionRunner(name) as React state. */
export function useActionRunner(name: string): UseActionRunnerResult {
  const runner = useMemo(() => client.createActionRunner(name), [name]);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<TakyonActionError | null>(null);

  const run = useCallback(
    async (payload: Record<string, unknown> = {}) => {
      setPending(true);
      setError(null);
      try {
        return await runner.run(payload);
      } catch (err) {
        // The kit classifies errors with .kind (budget, rate_limited,
        // already_running, unavailable, timeout, network, action_error)
        // and attaches .checkoutUrl on budget errors when checkout is callable.
        setError(err as TakyonActionError);
        return null;
      } finally {
        setPending(false);
      }
    },
    [runner],
  );

  return { run, pending, error };
}
