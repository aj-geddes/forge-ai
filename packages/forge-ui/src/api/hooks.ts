import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "./client";
import { getActivity } from "./activity";
import { listApprovals, approveApproval, rejectApproval } from "./approvals";
import type {
  ForgeConfig,
  HealthResponse,
  ToolInfo,
  Session,
  PeerAgent,
  OpenAPISource,
  PingPeerResponse,
  CreatePeerRequest,
} from "@/types/config";
import type { ApprovalDecisionResponse } from "@/types/approvals";

const ACTIVITY_POLL_INTERVAL_MS = 4_000;
const DEFAULT_ACTIVITY_LIMIT = 20;

// --- Query Keys ---

export const queryKeys = {
  health: ["health"] as const,
  config: ["config"] as const,
  configSchema: ["config", "schema"] as const,
  tools: ["tools"] as const,
  sessions: ["sessions"] as const,
  peers: ["peers"] as const,
  activity: (limit: number) => ["activity", limit] as const,
  approvals: ["approvals"] as const,
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

/**
 * Polls the admin activity feed (recent tool calls, newest-first) for the
 * Dashboard's Live Activity telemetry log. `retry: false` so a non-admin
 * caller's 403 surfaces as `isError` immediately instead of retry-looping --
 * the feed then renders a quiet "unavailable" state rather than crashing
 * (same posture as useSessions/usePeers for callers lacking admin reads).
 */
export function useActivity(limit: number = DEFAULT_ACTIVITY_LIMIT) {
  return useQuery({
    queryKey: queryKeys.activity(limit),
    queryFn: () => getActivity(limit),
    refetchInterval: ACTIVITY_POLL_INTERVAL_MS,
    retry: false,
  });
}

const APPROVALS_POLL_INTERVAL_MS = 4_000;

/**
 * Polls the human-in-the-loop approval queue (pending + recently decided
 * requests, newest state from the backend) for the Dashboard hero and the
 * Approvals page. `retry: false` so a caller lacking `config:read` surfaces
 * as `isError` immediately -- callers render a quiet "unavailable" state
 * (same posture as useActivity/useSessions/usePeers) rather than crashing.
 */
export function useApprovals() {
  return useQuery({
    queryKey: queryKeys.approvals,
    queryFn: listApprovals,
    refetchInterval: APPROVALS_POLL_INTERVAL_MS,
    retry: false,
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
    mutationFn: (source: OpenAPISource) =>
      api.post<{ tools: ToolInfo[]; count: number }>("/v1/admin/tools/preview", {
        source,
      }),
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
      api.post<PingPeerResponse>(`/v1/admin/peers/${name}/ping`),
  });
}

export function useCreatePeer() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (peer: CreatePeerRequest) =>
      api.post<PeerAgent>("/v1/admin/peers", peer),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.peers });
    },
  });
}

export function useApproveApproval() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (id: string): Promise<ApprovalDecisionResponse> => approveApproval(id),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.approvals });
    },
  });
}

export function useRejectApproval() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (id: string): Promise<ApprovalDecisionResponse> => rejectApproval(id),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.approvals });
    },
  });
}
