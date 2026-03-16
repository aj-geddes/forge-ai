import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "./client";
import type {
  ForgeConfig,
  HealthResponse,
  ToolInfo,
  Session,
  PeerAgent,
} from "@/types/config";

// --- Query Keys ---

export const queryKeys = {
  health: ["health"] as const,
  config: ["config"] as const,
  configSchema: ["config", "schema"] as const,
  tools: ["tools"] as const,
  sessions: ["sessions"] as const,
  peers: ["peers"] as const,
};

// --- Queries ---

export function useHealth() {
  return useQuery({
    queryKey: queryKeys.health,
    queryFn: () => api.get<HealthResponse>("/health/ready"),
    refetchInterval: 15_000,
    retry: false,
  });
}

export function useConfig() {
  return useQuery({
    queryKey: queryKeys.config,
    queryFn: async () => {
      const res = await api.get<{ config: ForgeConfig; path: string }>("/v1/admin/config");
      return res.config;
    },
  });
}

export function useConfigSchema() {
  return useQuery({
    queryKey: queryKeys.configSchema,
    queryFn: () => api.get<Record<string, unknown>>("/v1/admin/config/schema"),
  });
}

export function useTools() {
  return useQuery({
    queryKey: queryKeys.tools,
    queryFn: () => api.get<ToolInfo[]>("/v1/admin/tools"),
  });
}

export function useSessions() {
  return useQuery({
    queryKey: queryKeys.sessions,
    queryFn: () => api.get<Session[]>("/v1/admin/sessions"),
  });
}

export function usePeers() {
  return useQuery({
    queryKey: queryKeys.peers,
    queryFn: () => api.get<PeerAgent[]>("/v1/admin/peers"),
  });
}

// --- Mutations ---

export function useUpdateConfig() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (config: ForgeConfig) =>
      api.put<{ success: boolean; reloaded: boolean; message: string }>(
        "/v1/admin/config",
        { config },
      ),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.config });
    },
  });
}

export function useToolPreview() {
  return useMutation({
    mutationFn: (tool: { name: string; description: string; parameters?: unknown }) =>
      api.post<{ preview: string }>("/v1/admin/tools/preview", tool),
  });
}

export function useAddToolToConfig() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: async (
      updater: (tools: ForgeConfig["tools"]) => ForgeConfig["tools"],
    ) => {
      const res = await api.get<{ config: ForgeConfig; path: string }>(
        "/v1/admin/config",
      );
      const config = res.config;
      const updatedTools = updater(config.tools);
      const updatedConfig = { ...config, tools: updatedTools };
      return api.put<{ success: boolean; reloaded: boolean; message: string }>(
        "/v1/admin/config",
        { config: updatedConfig },
      );
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.config });
      void queryClient.invalidateQueries({ queryKey: queryKeys.tools });
    },
  });
}

export function useDeleteSession() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (id: string) => api.delete(`/v1/admin/sessions/${id}`),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.sessions });
    },
  });
}

export function usePingPeer() {
  return useMutation({
    mutationFn: (name: string) =>
      api.post<{ status: string; latency_ms: number }>(
        `/v1/admin/peers/${name}/ping`,
      ),
  });
}
