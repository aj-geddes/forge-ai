// Auth state for the BFF flow (ADR-0001). There is no client-held token --
// `GET /v1/auth/me` *is* the auth state, and TanStack Query is its cache.
// A 401 here is a normal, expected outcome ("not signed in"), so it is
// fetched with `skipAuthRedirect` -- the route guard decides what to render,
// not the fetch layer.

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "./client";
import type { Principal } from "@/types/auth";

export const authQueryKey = ["auth", "me"] as const;

export function useCurrentUser() {
  return useQuery({
    queryKey: authQueryKey,
    queryFn: () =>
      api.get<Principal>("/v1/auth/me", { skipAuthRedirect: true }),
    retry: false,
    staleTime: 60_000,
  });
}

export interface AuthState {
  user: Principal | null;
  isAuthenticated: boolean;
  isLoading: boolean;
  permissions: string[];
  /** `true` if the principal holds `permission`, or the `"*"` wildcard (admin). */
  can: (permission: string) => boolean;
}

export function useAuth(): AuthState {
  const query = useCurrentUser();
  const permissions = query.data?.permissions ?? [];

  return {
    user: query.data ?? null,
    isAuthenticated: query.isSuccess && query.data != null,
    isLoading: query.isLoading,
    permissions,
    can: (permission: string) =>
      permissions.includes("*") || permissions.includes(permission),
  };
}

export function useLogout() {
  const queryClient = useQueryClient();

  return useMutation({
    // POST /auth/logout is a state-changing, cookie-authenticated request --
    // api.post attaches the X-CSRF-Token header automatically (client.ts).
    mutationFn: () => api.post<void>("/auth/logout"),
    onSuccess: () => {
      // The session is gone server-side; drop the cached principal so the
      // route guard re-renders the login page. Do NOT navigate to
      // /auth/login here -- Dex's own SSO session survives a local logout
      // (ADR section 4.1), so an immediate redirect would silently sign the
      // user back in with no visible confirmation that logout happened.
      queryClient.setQueryData(authQueryKey, null);
      void queryClient.invalidateQueries({ queryKey: authQueryKey });
    },
  });
}
