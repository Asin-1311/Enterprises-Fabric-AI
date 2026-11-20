import { useCallback, useEffect, useState } from "react";

/** Load data once (and on `reload()`); `error` holds the API's message if it failed. */
export function useLoad<T>(loader: () => Promise<T>) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const reload = useCallback(() => {
    setError(null);
    loader().then(setData).catch((err: Error) => setError(err.message));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  useEffect(reload, [reload]);
  return { data, error, reload };
}

/** Run an admin action and report its outcome as a short message. */
export async function runAction(action: () => Promise<unknown>, done: string,
  setMessage: (m: { tone: "ok" | "error"; text: string }) => void, after?: () => void) {
  try {
    await action();
    setMessage({ tone: "ok", text: done });
    after?.();
  } catch (err) {
    setMessage({ tone: "error", text: (err as Error).message });
  }
}
