export const API_BASE = (
  import.meta.env.VITE_API_BASE_URL ||
  (import.meta.env.DEV ? "http://127.0.0.1:8000/api/v1" : "")
).replace(/\/$/, "");

let csrfToken: string | null = null;
let csrfRequest: Promise<string> | null = null;

export type AuthenticatedUser = {
  id: string;
  full_name: string;
  email: string;
  email_verified: boolean;
  onboarding_completed: boolean;
};

export class ApiRequestError extends Error {
  constructor(message: string, readonly status: number) {
    super(message);
  }
}

export function setCsrfToken(value: string | null): void {
  csrfToken = value;
}

export async function ensureCsrf(): Promise<string> {
  if (csrfToken) return csrfToken;
  if (csrfRequest) return csrfRequest;
  csrfRequest = (async () => {
    const response = await fetch(API_BASE + "/auth/csrf", { credentials: "include" });
    if (!response.ok) throw new Error("The security session could not be started. Check that the API is available.");
    const body = (await response.json()) as { csrf_token?: string };
    if (!body.csrf_token) throw new Error("The security session response was incomplete.");
    csrfToken = body.csrf_token;
    return csrfToken;
  })();
  try {
    return await csrfRequest;
  } finally {
    csrfRequest = null;
  }
}

export async function apiRequest<T>(path: string, init?: RequestInit): Promise<T> {
  const method = (init?.method || "GET").toUpperCase();
  const headers = new Headers(init?.headers);
  if (init?.body && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
  if (["POST", "PUT", "PATCH", "DELETE"].includes(method)) {
    headers.set("X-CSRF-Token", csrfToken || (await ensureCsrf()));
  }

  let response: Response;
  try {
    response = await fetch(API_BASE + path, { ...init, method, headers, credentials: "include" });
  } catch {
    throw new Error("Cannot reach the backend. Check that the API is running.");
  }
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    const detail = body?.error?.message || body?.detail?.message || body?.detail;
    const message = typeof detail === "string" ? detail : "The request failed (" + response.status + ").";
    if (response.status === 401 && !path.startsWith("/auth/")) {
      window.dispatchEvent(new CustomEvent("inscope:unauthenticated"));
    }
    throw new ApiRequestError(message, response.status);
  }
  if (response.status === 204) return undefined as T;
  const result = (await response.json()) as T;
  if (["/auth/login", "/auth/register"].includes(path) && typeof result === "object" && result && "csrf_token" in result) {
    setCsrfToken(String(result.csrf_token));
  }
  return result;
}
