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
  AgentDef,
  OpenAPISource,
  PingPeerResponse,
  CreatePeerRequest,
  AdminConfigGetResponse,
  ConfigMutationEnvelope,
  PromotionDiffResponse,
} from "@/types/config";
import type { ApprovalDecisionResponse } from "@/types/approvals";

const ACTIVITY_POLL_INTERVAL_MS = 4_000;
const DEFAULT_ACTIVITY_LIMIT = 20;

// --- Query Keys ---

export const queryKeys = {
  health: ["health"] as const,
  config: ["config"] as const,
  configSchema: ["config", "schema"] as const,
  configPromotionDiff: ["config", "promotion", "diff"] as const,
  tools: ["tools"] as const,
  sessions: ["sessions"] as const,
  peers: ["peers"] as const,
  agents: ["agents"] as const,
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

// GET /v1/admin/config now returns the full layered-config envelope
// (config + rev/base_rev/drift_from_git/source_layers/mutation_policy).
// `useConfig` stays backward compatible with existing callers -- `data`
// still resolves to the bare `ForgeConfig` via React Query's `select` --
// while `useConfigEnvelope` (below) exposes the honesty fields for the
// drift banner, promotion view, and optimistic-concurrency (If-Match).
// Both hooks share the same queryKey/queryFn, so React Query dedupes the
// underlying fetch between them.
export function useConfig() {
  return useQuery({
    queryKey: queryKeys.config,
    queryFn: () => api.get<AdminConfigGetResponse>("/v1/admin/config"),
    select: (data) => data.config,
  });
}

export function useConfigEnvelope() {
  return useQuery({
    queryKey: queryKeys.config,
    queryFn: () => api.get<AdminConfigGetResponse>("/v1/admin/config"),
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

// GET /v1/admin/agents returns the bare list of AgentDef entries (agents.
// agents) -- no honesty envelope, this is a read.
export function useAgents() {
  return useQuery({
    queryKey: queryKeys.agents,
    queryFn: () => api.get<AgentDef[]>("/v1/admin/agents"),
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
    mutationFn: ({ config, rev }: { config: ForgeConfig; rev: number }) =>
      api.put<ConfigMutationEnvelope>(
        "/v1/admin/config",
        { config },
        { headers: { "If-Match": String(rev) } },
      ),
    onSuccess: () => {
      // A 2xx here always means the backend attempted a real write per the
      // honesty-envelope contract -- refetch so the drift/rev state is
      // current. Whether to show a success affordance is the caller's call,
      // derived from `data.persisted` (see lib/mutationState.ts) -- this
      // hook never assumes success from the HTTP status alone.
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
      const res = await api.get<AdminConfigGetResponse>("/v1/admin/config");
      const updatedTools = updater(res.config.tools);
      const updatedConfig = { ...res.config, tools: updatedTools };
      return api.put<ConfigMutationEnvelope>(
        "/v1/admin/config",
        { config: updatedConfig },
        { headers: { "If-Match": String(res.rev) } },
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

// POST /v1/admin/peers (forge_gateway.routes.admin.create_peer) resolves the
// bare created-peer shape (AdminPeerResponse) -- unlike PUT /config and the
// tool/agent/peer PATCH/DELETE endpoints, it does NOT carry the
// persisted/durable/drift_from_git honesty envelope. Honesty is still
// enforced, just at the transport layer: the route runs the same
// apply_overlay_mutation() choke point and only reaches `return
// AdminPeerResponse(...)` on a genuine successful write -- a rejected write
// (409/405/507) always throws before that point, so a *resolved* mutate()
// here is unconditionally a real success. Callers must NOT run this
// response through deriveMutationUiState() (which expects a `persisted`
// field and would misread its absence as a rejection); only the error path
// needs deriveMutationUiState(undefined, err).
export function useCreatePeer() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (peer: CreatePeerRequest) =>
      api.post<PeerAgent>("/v1/admin/peers", peer),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.peers });
      void queryClient.invalidateQueries({ queryKey: queryKeys.config });
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

// --- Layered config: promotion diff + fine-grained tool/agent/peer mutations ---
//
// Every mutation below follows the same honesty contract as useUpdateConfig:
// it resolves a ConfigMutationEnvelope (or PeerMutationEnvelope), sends the
// caller's known revision as `If-Match` for optimistic concurrency, and only
// invalidates caches when `data.persisted` is genuinely true -- a 409
// (conflict), 405 (mutation_policy: disabled), or 507 (write rejected) all
// surface as a thrown ApiError from `onError`, never as a false "something
// changed" cache invalidation.

export function useConfigPromotionDiff(options?: { enabled?: boolean }) {
  return useQuery({
    queryKey: queryKeys.configPromotionDiff,
    queryFn: () => api.get<PromotionDiffResponse>("/v1/admin/config/promotion/diff"),
    enabled: options?.enabled ?? true,
  });
}

export function useUpdateTool() {
  const queryClient = useQueryClient();

  return useMutation({
    // Backend route is PATCH /v1/admin/tools/{name} (forge_gateway.routes.
    // admin.update_tool) -- there is no PUT for the per-entity endpoints,
    // only for the whole-config replace (PUT /v1/admin/config).
    mutationFn: ({ name, tool, rev }: { name: string; tool: Record<string, unknown>; rev: number }) =>
      api.patch<ConfigMutationEnvelope>(`/v1/admin/tools/${encodeURIComponent(name)}`, tool, {
        headers: { "If-Match": String(rev) },
      }),
    onSuccess: (data) => {
      if (data.persisted) {
        void queryClient.invalidateQueries({ queryKey: queryKeys.tools });
        void queryClient.invalidateQueries({ queryKey: queryKeys.config });
      }
    },
  });
}

export function useDeleteTool() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({ name, rev }: { name: string; rev: number }) =>
      api.delete<ConfigMutationEnvelope>(`/v1/admin/tools/${encodeURIComponent(name)}`, {
        headers: { "If-Match": String(rev) },
      }),
    onSuccess: (data) => {
      if (data.persisted) {
        void queryClient.invalidateQueries({ queryKey: queryKeys.tools });
        void queryClient.invalidateQueries({ queryKey: queryKeys.config });
      }
    },
  });
}

// POST /v1/admin/agents (forge_gateway.routes.admin.create_agent) returns
// AdminConfigUpdateResponse directly -- UNLIKE POST /v1/admin/peers, this
// route DOES carry the full persisted/durable/drift_from_git honesty
// envelope (agents.agents has no destination/secret to gate specially, so
// there is no bare-shape shortcut here). Callers must run the result
// through deriveMutationUiState() exactly like useUpdateAgent/useDeleteAgent
// -- never assume success from a resolved mutate() alone.
export function useCreateAgent() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({ agent, rev }: { agent: Record<string, unknown>; rev?: number }) =>
      api.post<ConfigMutationEnvelope>("/v1/admin/agents", agent, {
        headers: rev !== undefined ? { "If-Match": String(rev) } : undefined,
      }),
    onSuccess: (data) => {
      if (data.persisted) {
        void queryClient.invalidateQueries({ queryKey: queryKeys.agents });
        void queryClient.invalidateQueries({ queryKey: queryKeys.config });
      }
    },
  });
}

export function useUpdateAgent() {
  const queryClient = useQueryClient();

  return useMutation({
    // Backend route is PATCH /v1/admin/agents/{name}.
    mutationFn: ({ name, agent, rev }: { name: string; agent: Record<string, unknown>; rev: number }) =>
      api.patch<ConfigMutationEnvelope>(`/v1/admin/agents/${encodeURIComponent(name)}`, agent, {
        headers: { "If-Match": String(rev) },
      }),
    onSuccess: (data) => {
      if (data.persisted) {
        void queryClient.invalidateQueries({ queryKey: queryKeys.agents });
        void queryClient.invalidateQueries({ queryKey: queryKeys.config });
      }
    },
  });
}

export function useDeleteAgent() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({ name, rev }: { name: string; rev: number }) =>
      api.delete<ConfigMutationEnvelope>(`/v1/admin/agents/${encodeURIComponent(name)}`, {
        headers: { "If-Match": String(rev) },
      }),
    onSuccess: (data) => {
      if (data.persisted) {
        void queryClient.invalidateQueries({ queryKey: queryKeys.agents });
        void queryClient.invalidateQueries({ queryKey: queryKeys.config });
      }
    },
  });
}

export function useUpdatePeer() {
  const queryClient = useQueryClient();

  return useMutation({
    // Backend route is PATCH /v1/admin/peers/{name}, returning the plain
    // honesty envelope (AdminConfigUpdateResponse) -- unlike POST /peers,
    // it does NOT nest the updated peer under a `peer` field.
    mutationFn: ({ name, peer, rev }: { name: string; peer: Record<string, unknown>; rev: number }) =>
      api.patch<ConfigMutationEnvelope>(`/v1/admin/peers/${encodeURIComponent(name)}`, peer, {
        headers: { "If-Match": String(rev) },
      }),
    onSuccess: (data) => {
      if (data.persisted) {
        void queryClient.invalidateQueries({ queryKey: queryKeys.peers });
        void queryClient.invalidateQueries({ queryKey: queryKeys.config });
      }
    },
  });
}

export function useDeletePeer() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({ name, rev }: { name: string; rev: number }) =>
      api.delete<ConfigMutationEnvelope>(`/v1/admin/peers/${encodeURIComponent(name)}`, {
        headers: { "If-Match": String(rev) },
      }),
    onSuccess: (data) => {
      if (data.persisted) {
        void queryClient.invalidateQueries({ queryKey: queryKeys.peers });
        void queryClient.invalidateQueries({ queryKey: queryKeys.config });
      }
    },
  });
}
