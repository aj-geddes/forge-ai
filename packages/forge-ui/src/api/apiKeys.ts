// Self-service user-issued API keys (ADR-0002). BFF-mode, same as the rest
// of the API layer (client.ts) -- the session cookie authenticates and
// api.post/api.delete auto-attach X-CSRF-Token; no bearer token is ever
// held or sent by the UI.

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "./client";
import type {
  ApiKeySummary,
  CreateApiKeyRequest,
  MintedApiKey,
} from "@/types/apiKeys";

export const apiKeysQueryKey = ["apiKeys"] as const;

/**
 * Lists the caller's own API keys. The gateway's response envelope is
 * `{tokens: TokenListItem[]}` (forge_gateway/models.py TokenListResponse),
 * not a bare array -- this hook unwraps it.
 */
export function useApiKeys() {
  return useQuery({
    queryKey: apiKeysQueryKey,
    queryFn: async () => {
      const res = await api.get<{ tokens: ApiKeySummary[] }>(
        "/v1/auth/tokens",
      );
      return res.tokens;
    },
  });
}

/**
 * Mints a new API key. The resolved `MintedApiKey` carries the raw secret
 * (`token`) exactly once -- callers must render it and then discard it
 * (e.g. component state cleared on dialog dismiss), never persist it.
 */
export function useCreateApiKey() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (payload: CreateApiKeyRequest) =>
      api.post<MintedApiKey>("/v1/auth/tokens", payload),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: apiKeysQueryKey });
    },
  });
}

export function useRevokeApiKey() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (id: string) => api.delete<void>(`/v1/auth/tokens/${id}`),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: apiKeysQueryKey });
    },
  });
}
