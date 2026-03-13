import { useState, useCallback } from "react";
import {
  Network,
  Plus,
  Activity,
  Globe,
  ShieldCheck,
  Loader2,
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
import { usePeers, usePingPeer } from "@/api/hooks";
import type { TrustLevel } from "@/types/config";

type ConnectionStatus = "reachable" | "unreachable" | "unknown";

interface PeerPingState {
  status: ConnectionStatus;
  latency?: number;
  loading: boolean;
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
  peer: { name: string; url: string; trust_level: TrustLevel; capabilities?: string[] };
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
          {peer.url}
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
        <div className="flex items-center justify-between pt-1">
          <div className="text-xs text-muted-foreground">
            {pingState.status === "reachable" && pingState.latency != null && (
              <span className="flex items-center gap-1">
                <Activity className="h-3 w-3" />
                {pingState.latency}ms
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
          >
            {pingState.loading ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : (
              <Activity className="h-3.5 w-3.5" />
            )}
            Ping
          </Button>
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

  const handleSubmit = useCallback(() => {
    // For now, log the peer data. Integration with a mutation will be added
    // when the backend supports POST /v1/admin/peers.
    const _peerData = {
      name,
      url: endpoint,
      trust_level: trustLevel,
      capabilities: capabilities
        .split(",")
        .map((c) => c.trim())
        .filter(Boolean),
    };
    void _peerData;
    setOpen(false);
    setName("");
    setEndpoint("");
    setTrustLevel("medium");
    setCapabilities("");
  }, [name, endpoint, trustLevel, capabilities]);

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger className="inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground transition-colors hover:bg-primary/90">
        <Plus className="h-4 w-4" />
        Add Peer
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Add Peer Agent</DialogTitle>
          <DialogDescription>
            Configure a new A2A peer agent connection.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-4 py-4">
          <div className="space-y-2">
            <Label htmlFor="peer-name">Name</Label>
            <Input
              id="peer-name"
              placeholder="my-peer-agent"
              value={name}
              onChange={(e) => setName(e.target.value)}
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="peer-endpoint">Endpoint URL</Label>
            <Input
              id="peer-endpoint"
              placeholder="https://peer.example.com/a2a"
              value={endpoint}
              onChange={(e) => setEndpoint(e.target.value)}
            />
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
          </div>
          <div className="space-y-2">
            <Label htmlFor="peer-capabilities">Capabilities</Label>
            <Input
              id="peer-capabilities"
              placeholder="search, summarize, translate"
              value={capabilities}
              onChange={(e) => setCapabilities(e.target.value)}
            />
            <p className="text-xs text-muted-foreground">
              Comma-separated list of capabilities
            </p>
          </div>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => setOpen(false)}>
            Cancel
          </Button>
          <Button onClick={handleSubmit} disabled={!name.trim() || !endpoint.trim()}>
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
              status: data.status === "ok" ? "reachable" : "unreachable",
              latency: data.latency_ms,
              loading: false,
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
            <p className="text-sm text-muted-foreground">
              Manage A2A peer agent connections
            </p>
          </div>
        </div>
        <AddPeerDialog />
      </div>

      {!peers || peers.length === 0 ? (
        <Card>
          <CardContent className="py-12">
            <div className="flex flex-col items-center gap-3 text-center">
              <ShieldCheck className="h-12 w-12 text-muted-foreground/50" />
              <div>
                <p className="text-lg font-medium text-muted-foreground">
                  No peers configured
                </p>
                <p className="mt-1 text-sm text-muted-foreground">
                  Add a peer agent to enable agent-to-agent communication.
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
