export type ScopedResource<T> = {
  scope: string;
  data: T | null;
  loading: boolean;
  error: string | null;
};

export type ScopedRequestResult<T> =
  | { ok: true; value: T }
  | { ok: false; error: string };

export function scopedResourceFor<T>(
  resource: ScopedResource<T>,
  scope: string,
  empty: T,
): ScopedResource<T> {
  if (resource.scope === scope) return resource;
  return { scope, data: empty, loading: Boolean(scope), error: null };
}

export async function loadScopedResource<T>(
  load: () => Promise<T>,
  isCurrent: () => boolean,
): Promise<ScopedRequestResult<T> | null> {
  try {
    const value = await load();
    return isCurrent() ? { ok: true, value } : null;
  } catch (reason) {
    if (!isCurrent()) return null;
    return {
      ok: false,
      error: reason instanceof Error ? reason.message : "The request failed.",
    };
  }
}
