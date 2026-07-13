import { useState, useCallback } from "react";
import {
  Network,
  Plus,
  Activity,
  Globe,
  ShieldCheck,
  Loader2,
  Info,
  AlertCircle,
} from "lucide-react";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select } from "@/components/ui/select";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { cn } from "@/lib/utils";
import { ApiError } from "@/api/client";
import { usePeers, usePingPeer, useCreatePeer } from "@/api/hooks";
import type { TrustLevel } from "@/types/config";

type ConnectionStatus = "reachable" | "unreachable" | "unknown";

interface PeerPingState {
  status: ConnectionStatus;
  loading: boolean;
  latencyMs?: number | null;
}

function HelpText({ children }: { children: React.ReactNode }) {
  return (
    <p className="flex items-start gap-1.5 text-xs text-muted-foreground mt-1 leading-relaxed">
      <Info className="h-3 w-3 mt-0.5 shrink-0 opacity-60" />
      <span>{children}</span>
    </p>
  );
}

const trustLevelColors: Record<TrustLevel, string> = {
  high: "bg-green-500/15 text-green-700 border-green-500/30",
  medium: "bg-yellow-500/15 text-yellow-700 border-yellow-500/30",
  low: "bg-red-500/15 text-red-700 border-red-500/30",
};

const statusDotColors: Record<ConnectionStatus, string> = {
  reachable: "bg-green-500",
  unreachable: "bg-red-500",
  unknown: "bg-gray-400",
};

function StatusDot({ status }: { status: ConnectionStatus }) {
  return (
    <span className="relative flex h-3 w-3">
      {status === "reachable" && (
        <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-green-400 opacity-75" />
      )}
      <span
        className={cn(
          "relative inline-flex h-3 w-3 rounded-full",
          statusDotColors[status],
        )}
      />
    </span>
  );
}

function PeerCard({
  peer,
  pingState,
  onPing,
}: {
  peer: { name: string; endpoint: string; trust_level: TrustLevel; capabilities?: string[] };
  pingState: PeerPingState;
  onPing: () => void;
}) {
  return (
    <Card>
      <CardHeader className="pb-3">
        <div className="flex items-start justify-between">
          <div className="flex items-center gap-2">
            <StatusDot status={pingState.status} />
            <CardTitle className="text-lg">{peer.name}</CardTitle>
          </div>
          <Badge
            variant="outline"
            className={cn("capitalize", trustLevelColors[peer.trust_level])}
          >
            {peer.trust_level}
          </Badge>
        </div>
        <CardDescription className="flex items-center gap-1.5 pt-1">
          <Globe className="h-3.5 w-3.5" />
          {peer.endpoint}
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        {peer.capabilities && peer.capabilities.length > 0 && (
          <div className="flex flex-wrap gap-1.5">
            {peer.capabilities.map((cap) => (
              <Badge key={cap} variant="secondary" className="text-xs">
                {cap}
              </Badge>
            ))}
          </div>
        )}
        <div className="space-y-2 pt-1">
          <div className="flex items-center justify-between">
            <div className="text-xs text-muted-foreground">
              {pingState.status === "reachable" && (
                <span className="flex items-center gap-1 text-[oklch(0.5_0.2_145)]">
                  <Activity className="h-3 w-3" />
                  Reachable
                  {pingState.latencyMs != null && (
                    <span className="text-muted-foreground">
                      ({pingState.latencyMs} ms)
                    </span>
                  )}
                </span>
              )}
              {pingState.status === "unreachable" && (
                <span className="text-destructive">Unreachable</span>
              )}
              {pingState.status === "unknown" && (
                <span>Status unknown</span>
              )}
            </div>
            <Button
              variant="outline"
              size="sm"
              onClick={onPing}
              disabled={pingState.loading}
              title="Test connectivity to this peer"
            >
              {pingState.loading ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
              ) : (
                <Activity className="h-3.5 w-3.5" />
              )}
              Ping
            </Button>
          </div>
          <p className="text-[11px] text-muted-foreground/70">
            Tests connectivity to this peer&apos;s /health/live endpoint
          </p>
        </div>
      </CardContent>
    </Card>
  );
}

function AddPeerDialog() {
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [endpoint, setEndpoint] = useState("");
  const [trustLevel, setTrustLevel] = useState<TrustLevel>("medium");
  const [capabilities, setCapabilities] = useState("");
  const [spiffeId, setSpiffeId] = useState("");
  const [submitError, setSubmitError] = useState<string | null>(null);

  const createPeer = useCreatePeer();

  const canSubmit = name.trim().length > 0 && endpoint.trim().length > 0;

  const resetForm = useCallback(() => {
    setName("");
    setEndpoint("");
    setTrustLevel("medium");
    setCapabilities("");
    setSpiffeId("");
    setSubmitError(null);
  }, []);

  const handleSubmit = useCallback(() => {
    if (!canSubmit) return;
    setSubmitError(null);

    createPeer.mutate(
      {
        name: name.trim(),
        endpoint: endpoint.trim(),
        trust_level: trustLevel,
        capabilities: capabilities
          .split(",")
          .map((c) => c.trim())
          .filter(Boolean),
        spiffe_id: spiffeId.trim() || undefined,
      },
      {
        onSuccess: () => {
          setOpen(false);
          resetForm();
        },
        onError: (err) => {
          setSubmitError(
            err instanceof ApiError
              ? err.message
              : err instanceof Error
                ? err.message
                : "Failed to add peer",
          );
        },
      },
    );
  }, [canSubmit, createPeer, name, endpoint, trustLevel, capabilities, spiffeId, resetForm]);

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        setOpen(next);
        if (!next) setSubmitError(null);
      }}
    >
      <DialogTrigger className="inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground transition-colors hover:bg-primary/90">
        <Plus className="h-4 w-4" />
        Add Peer
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Add Peer Agent</DialogTitle>
          <DialogDescription>
            Register a new Agent-to-Agent (A2A) peer connection. Peers are
            other Forge agents your agent can delegate tasks to, enabling
            multi-agent collaboration and specialization.
          </DialogDescription>
        </DialogHeader>
        {submitError && (
          <div className="flex items-start gap-2 rounded-lg border border-destructive/30 bg-destructive/5 px-3 py-2 text-xs text-destructive">
            <AlertCircle className="h-3.5 w-3.5 mt-0.5 shrink-0" />
            <span>{submitError}</span>
          </div>
        )}
        <div className="space-y-4 py-4">
          <div className="space-y-2">
            <Label htmlFor="peer-name">Name</Label>
            <Input
              id="peer-name"
              placeholder="my-peer-agent"
              value={name}
              onChange={(e) => setName(e.target.value)}
            />
            <HelpText>
              A unique identifier used when routing requests to this peer.
              Use a descriptive, lowercase name (e.g., "research-agent" or
              "code-reviewer").
            </HelpText>
          </div>
          <div className="space-y-2">
            <Label htmlFor="peer-endpoint">Endpoint URL</Label>
            <Input
              id="peer-endpoint"
              placeholder="https://peer.example.com/a2a"
              value={endpoint}
              onChange={(e) => setEndpoint(e.target.value)}
            />
            <HelpText>
              The base URL of the peer agent's A2A interface. This is
              where your agent will send requests. Must be reachable from
              your agent's network -- endpoints targeting private or
              internal networks (e.g., 127.0.0.1, 10.x.x.x, *.local) are
              blocked by SSRF protection.
            </HelpText>
          </div>
          <div className="space-y-2">
            <Label htmlFor="peer-trust">Trust Level</Label>
            <Select
              id="peer-trust"
              value={trustLevel}
              onChange={(e) => setTrustLevel(e.target.value as TrustLevel)}
            >
              <option value="high">High</option>
              <option value="medium">Medium</option>
              <option value="low">Low</option>
            </Select>
            <HelpText>
              Controls what this peer can access. High = fully trusted,
              can access all capabilities. Medium = limited trust, may be
              rate-limited. Low = untrusted, heavily restricted with strict
              rate limits.
            </HelpText>
          </div>
          <div className="space-y-2">
            <Label htmlFor="peer-capabilities">Capabilities</Label>
            <Input
              id="peer-capabilities"
              placeholder="search, summarize, translate"
              value={capabilities}
              onChange={(e) => setCapabilities(e.target.value)}
            />
            <HelpText>
              Comma-separated list of skills this peer offers (e.g., "search,
              summarize, translate"). These declared capabilities are used for
              intelligent routing -- your agent will match incoming tasks to
              the peer best suited to handle them.
            </HelpText>
          </div>
          <div className="space-y-2">
            <Label htmlFor="peer-spiffe-id">SPIFFE ID (optional)</Label>
            <Input
              id="peer-spiffe-id"
              placeholder="spiffe://forge.local/peer/my-peer-agent"
              value={spiffeId}
              onChange={(e) => setSpiffeId(e.target.value)}
            />
            <HelpText>
              The peer's expected SPIFFE ID on the workload (A2A mTLS) plane.
              Required when AgentWeave is enabled in Security settings --
              without it, the peer call has nothing to verify the
              responding workload's identity against.
            </HelpText>
          </div>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => setOpen(false)}>
            Close
          </Button>
          <Button
            onClick={handleSubmit}
            disabled={!canSubmit || createPeer.isPending}
          >
            {createPeer.isPending ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : null}
            Add Peer
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export function PeersPage() {
  const { data: peers, isLoading, error } = usePeers();
  const pingMutation = usePingPeer();
  const [pingStates, setPingStates] = useState<Record<string, PeerPingState>>(
    {},
  );

  const handlePing = useCallback(
    (peerName: string) => {
      setPingStates((prev) => ({
        ...prev,
        [peerName]: { ...prev[peerName], status: prev[peerName]?.status ?? "unknown", loading: true },
      }));

      pingMutation.mutate(peerName, {
        onSuccess: (data) => {
          setPingStates((prev) => ({
            ...prev,
            [peerName]: {
              // The backend's ping response (AdminPeerStatus) returns the literal
              // "reachable" / "unreachable" -- it never returns "ok". When
              // reachable, it also reports latency_ms (the round-trip time of
              // the /health/live check); unreachable pings never carry a
              // meaningful latency figure.
              status: data.status,
              loading: false,
              latencyMs: data.status === "reachable" ? (data.latency_ms ?? null) : null,
            },
          }));
        },
        onError: () => {
          setPingStates((prev) => ({
            ...prev,
            [peerName]: { status: "unreachable", loading: false },
          }));
        },
      });
    },
    [pingMutation],
  );

  const getPingState = useCallback(
    (name: string): PeerPingState =>
      pingStates[name] ?? { status: "unknown" as const, loading: false },
    [pingStates],
  );

  if (isLoading) {
    return (
      <div className="flex min-h-[400px] items-center justify-center">
        <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
      </div>
    );
  }

  if (error) {
    return (
      <Card>
        <CardContent className="py-8">
          <p className="text-center text-sm text-destructive">
            Failed to load peers: {error.message}
          </p>
        </CardContent>
      </Card>
    );
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <Network className="h-8 w-8 text-muted-foreground" />
          <div>
            <h1 className="text-2xl font-semibold tracking-tight">Peers</h1>
            <p className="text-sm text-muted-foreground max-w-xl">
              Manage Agent-to-Agent (A2A) peer connections. Connect to other
              Forge agents to delegate specialized tasks, share capabilities,
              or build multi-agent workflows.
            </p>
          </div>
        </div>
        <AddPeerDialog />
      </div>

      {!peers || peers.length === 0 ? (
        <Card>
          <CardContent className="py-12">
            <div className="flex flex-col items-center gap-4 text-center">
              <ShieldCheck className="h-12 w-12 text-muted-foreground/50" />
              <div className="max-w-md">
                <p className="text-lg font-medium text-muted-foreground">
                  No peers configured
                </p>
                <p className="mt-1 text-sm text-muted-foreground">
                  Peers let your agent call other agents for tasks outside its
                  expertise. For example, a coding agent could delegate research
                  to a search-specialized peer, or hand off translations to a
                  language peer.
                </p>
                <p className="mt-3 text-sm text-muted-foreground">
                  Add your first peer to start building a collaborative
                  multi-agent network.
                </p>
              </div>
            </div>
          </CardContent>
        </Card>
      ) : (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {peers.map((peer) => (
            <PeerCard
              key={peer.name}
              peer={peer}
              pingState={getPingState(peer.name)}
              onPing={() => handlePing(peer.name)}
            />
          ))}
        </div>
      )}
    </div>
  );
}
