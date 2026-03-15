import { useAuthStore } from "@/stores/authStore";

const API_BASE = "";

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

export async function apiRequest<T>(
  path: string,
  options?: RequestInit,
): Promise<T> {
  const apiKey = useAuthStore.getState().apiKey;
  const authHeaders: Record<string, string> = apiKey
    ? { Authorization: `Bearer ${apiKey}` }
    : {};

  const res = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...authHeaders,
      ...options?.headers,
    },
  });

  if (!res.ok) {
    if (res.status === 401) {
      useAuthStore.getState().logout();
    }
    const error = await res.json().catch(() => ({ detail: res.statusText }));
    throw new ApiError(
      res.status,
      error.detail || error.error || "Request failed",
    );
  }

  return res.json() as Promise<T>;
}

export const api = {
  get: <T>(path: string) => apiRequest<T>(path),

  post: <T>(path: string, body?: unknown) =>
    apiRequest<T>(path, {
      method: "POST",
      body: body ? JSON.stringify(body) : undefined,
    }),

  put: <T>(path: string, body?: unknown) =>
    apiRequest<T>(path, {
      method: "PUT",
      body: body ? JSON.stringify(body) : undefined,
    }),

  delete: <T>(path: string) =>
    apiRequest<T>(path, { method: "DELETE" }),
};
