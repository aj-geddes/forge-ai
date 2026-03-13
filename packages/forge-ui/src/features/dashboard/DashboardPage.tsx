import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import {
  Wrench,
  MessageSquare,
  Network,
  Settings,
  Plus,
  RefreshCw,
  Heart,
  CheckCircle2,
  XCircle,
  AlertTriangle,
  ArrowRight,
  Server,
  Tag,
  Layers,
  Cpu,
  Clock,
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
import { Separator } from "@/components/ui/separator";
import {
  useHealth,
  useConfig,
  useTools,
  useSessions,
  usePeers,
} from "@/api/hooks";
import { api } from "@/api/client";
import { cn } from "@/lib/utils";
import type { HealthResponse } from "@/types/config";

// ---------------------------------------------------------------------------
// Skeleton helpers
// ---------------------------------------------------------------------------

function SkeletonLine({ className }: { className?: string }) {
  return (
    <div
      className={cn("animate-pulse rounded bg-muted", className)}
    />
  );
}

function StatCardSkeleton() {
  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
        <SkeletonLine className="h-4 w-24" />
        <SkeletonLine className="h-4 w-4 rounded-full" />
      </CardHeader>
      <CardContent>
        <div className="space-y-2">
          <SkeletonLine className="h-8 w-20" />
          <SkeletonLine className="h-4 w-32" />
        </div>
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Stat Card
// ---------------------------------------------------------------------------

type HealthIndicator = "healthy" | "unhealthy" | "unknown";

function StatCard({
  title,
  value,
  description,
  icon: Icon,
  loading,
  error,
  status,
}: {
  title: string;
  value: string | number;
  description: string;
  icon: React.ComponentType<{ className?: string }>;
  loading?: boolean;
  error?: boolean;
  status?: HealthIndicator;
}) {
  if (loading) {
    return <StatCardSkeleton />;
  }

  const statusColor: Record<HealthIndicator, string> = {
    healthy: "text-[oklch(0.65_0.2_145)]",      // green
    unhealthy: "text-destructive",
    unknown: "text-muted-foreground",
  };

  const statusBg: Record<HealthIndicator, string> = {
    healthy: "bg-[oklch(0.65_0.2_145/0.1)]",
    unhealthy: "bg-destructive/10",
    unknown: "bg-muted",
  };

  return (
    <Card className="transition-shadow hover:shadow-md">
      <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
        <CardTitle className="text-sm font-medium">{title}</CardTitle>
        <div
          className={cn(
            "flex h-8 w-8 items-center justify-center rounded-lg",
            status ? statusBg[status] : "bg-primary/10",
          )}
        >
          <Icon
            className={cn(
              "h-4 w-4",
              status ? statusColor[status] : "text-primary",
            )}
          />
        </div>
      </CardHeader>
      <CardContent>
        {error ? (
          <div className="flex items-center gap-2 text-destructive">
            <AlertTriangle className="h-4 w-4" />
            <span className="text-sm">Failed to load</span>
          </div>
        ) : (
          <>
            <div className="flex items-center gap-2">
              <div className="text-2xl font-bold tracking-tight">{value}</div>
              {status && (
                <Badge
                  variant={
                    status === "healthy"
                      ? "default"
                      : status === "unhealthy"
                        ? "destructive"
                        : "secondary"
                  }
                  className="capitalize"
                >
                  {status === "healthy" ? "Healthy" : status === "unhealthy" ? "Degraded" : "Unknown"}
                </Badge>
              )}
            </div>
            <p className="mt-1 text-xs text-muted-foreground">{description}</p>
          </>
        )}
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Health Check Row (for live/ready/startup detail)
// ---------------------------------------------------------------------------

function HealthCheckRow({
  label,
  endpoint,
}: {
  label: string;
  endpoint: string;
}) {
  const { data, isLoading, isError } = useQuery({
    queryKey: ["health", endpoint],
    queryFn: () => api.get<HealthResponse>(endpoint),
    refetchInterval: 30_000,
    retry: 1,
  });

  const isOk = data?.status === "ok" || data?.status === "healthy";

  return (
    <div className="flex items-center justify-between rounded-lg border px-4 py-3 transition-colors hover:bg-muted/50">
      <div className="flex items-center gap-3">
        {isLoading ? (
          <div className="h-5 w-5 animate-pulse rounded-full bg-muted" />
        ) : isError ? (
          <XCircle className="h-5 w-5 text-destructive" />
        ) : isOk ? (
          <CheckCircle2 className="h-5 w-5 text-[oklch(0.65_0.2_145)]" />
        ) : (
          <AlertTriangle className="h-5 w-5 text-[oklch(0.75_0.15_70)]" />
        )}
        <div>
          <span className="text-sm font-medium">{label}</span>
          <p className="text-xs text-muted-foreground">{endpoint}</p>
        </div>
      </div>
      <div className="flex items-center gap-2">
        {isLoading ? (
          <SkeletonLine className="h-5 w-16" />
        ) : isError ? (
          <Badge variant="destructive">Unreachable</Badge>
        ) : (
          <Badge
            variant={isOk ? "default" : "destructive"}
            className={cn(
              isOk && "bg-[oklch(0.65_0.2_145)] hover:bg-[oklch(0.6_0.2_145)]",
            )}
          >
            {data?.status ?? "unknown"}
          </Badge>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Quick Action Button
// ---------------------------------------------------------------------------

function QuickAction({
  to,
  icon: Icon,
  label,
  description,
}: {
  to: string;
  icon: React.ComponentType<{ className?: string }>;
  label: string;
  description: string;
}) {
  return (
    <Link to={to} className="group block flex-1">
      <div className="flex items-center gap-4 rounded-lg border p-4 transition-all hover:border-primary/30 hover:bg-muted/50 hover:shadow-sm">
        <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-primary/10 transition-colors group-hover:bg-primary/20">
          <Icon className="h-5 w-5 text-primary" />
        </div>
        <div className="min-w-0 flex-1">
          <p className="text-sm font-medium">{label}</p>
          <p className="text-xs text-muted-foreground">{description}</p>
        </div>
        <ArrowRight className="h-4 w-4 text-muted-foreground transition-transform group-hover:translate-x-0.5" />
      </div>
    </Link>
  );
}

// ---------------------------------------------------------------------------
// System Info Card
// ---------------------------------------------------------------------------

function SystemInfoSkeleton() {
  return (
    <Card>
      <CardHeader>
        <SkeletonLine className="h-5 w-32" />
        <SkeletonLine className="mt-1 h-4 w-48" />
      </CardHeader>
      <CardContent>
        <div className="space-y-4">
          {Array.from({ length: 4 }).map((_, i) => (
            <div key={i} className="flex items-center justify-between">
              <SkeletonLine className="h-4 w-24" />
              <SkeletonLine className="h-4 w-32" />
            </div>
          ))}
        </div>
      </CardContent>
    </Card>
  );
}

function SystemInfoCard() {
  const { data: config, isLoading, isError } = useConfig();

  if (isLoading) {
    return <SystemInfoSkeleton />;
  }

  if (isError || !config) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="text-base">System Information</CardTitle>
          <CardDescription>Configuration metadata</CardDescription>
        </CardHeader>
        <CardContent>
          <div className="flex items-center gap-2 text-sm text-destructive">
            <AlertTriangle className="h-4 w-4" />
            <span>Unable to load configuration data</span>
          </div>
        </CardContent>
      </Card>
    );
  }

  const infoRows: { icon: React.ComponentType<{ className?: string }>; label: string; value: string }[] = [
    { icon: Server, label: "Name", value: config.metadata.name },
    { icon: Tag, label: "Version", value: config.metadata.version },
    { icon: Cpu, label: "Default Model", value: config.llm.model },
    {
      icon: Layers,
      label: "LiteLLM Mode",
      value: config.llm.litellm?.mode ?? "N/A",
    },
  ];

  if (config.metadata.description) {
    infoRows.push({
      icon: MessageSquare,
      label: "Description",
      value: config.metadata.description,
    });
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">System Information</CardTitle>
        <CardDescription>
          Configuration metadata for your Forge instance
        </CardDescription>
      </CardHeader>
      <CardContent>
        <div className="space-y-1">
          {infoRows.map((row, i) => (
            <div key={row.label}>
              <div className="flex items-center justify-between py-2.5">
                <div className="flex items-center gap-2.5 text-sm text-muted-foreground">
                  <row.icon className="h-4 w-4" />
                  <span>{row.label}</span>
                </div>
                <span className="text-sm font-medium">{row.value}</span>
              </div>
              {i < infoRows.length - 1 && <Separator />}
            </div>
          ))}
        </div>
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Health Checks Detail Card (individual subsystem checks from /health/ready)
// ---------------------------------------------------------------------------

function SubsystemChecksSkeleton() {
  return (
    <div className="space-y-2">
      {Array.from({ length: 3 }).map((_, i) => (
        <div key={i} className="flex items-center justify-between rounded-lg border px-4 py-3">
          <div className="flex items-center gap-3">
            <SkeletonLine className="h-5 w-5 rounded-full" />
            <div className="space-y-1">
              <SkeletonLine className="h-4 w-24" />
              <SkeletonLine className="h-3 w-32" />
            </div>
          </div>
          <SkeletonLine className="h-5 w-16" />
        </div>
      ))}
    </div>
  );
}

function SubsystemChecks() {
  const { data: health, isLoading } = useHealth();

  if (isLoading) {
    return <SubsystemChecksSkeleton />;
  }

  if (!health?.checks || Object.keys(health.checks).length === 0) {
    return (
      <p className="py-4 text-center text-sm text-muted-foreground">
        No subsystem checks reported
      </p>
    );
  }

  return (
    <div className="space-y-2">
      {Object.entries(health.checks).map(([name, check]) => {
        const isOk = check.status === "ok" || check.status === "healthy";
        return (
          <div
            key={name}
            className="flex items-center justify-between rounded-lg border px-4 py-3 transition-colors hover:bg-muted/50"
          >
            <div className="flex items-center gap-3">
              {isOk ? (
                <CheckCircle2 className="h-5 w-5 text-[oklch(0.65_0.2_145)]" />
              ) : (
                <XCircle className="h-5 w-5 text-destructive" />
              )}
              <div>
                <span className="text-sm font-medium capitalize">{name}</span>
                {check.message && (
                  <p className="text-xs text-muted-foreground">
                    {check.message}
                  </p>
                )}
              </div>
            </div>
            <Badge
              variant={isOk ? "default" : "destructive"}
              className={cn(
                isOk && "bg-[oklch(0.65_0.2_145)] hover:bg-[oklch(0.6_0.2_145)]",
              )}
            >
              {check.status}
            </Badge>
          </div>
        );
      })}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Uptime display helper
// ---------------------------------------------------------------------------

function formatUptime(seconds: number): string {
  if (seconds < 60) return `${Math.floor(seconds)}s`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ${Math.floor(seconds % 60)}s`;
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  if (hours < 24) return `${hours}h ${minutes}m`;
  const days = Math.floor(hours / 24);
  return `${days}d ${hours % 24}h`;
}

// ---------------------------------------------------------------------------
// Main Dashboard
// ---------------------------------------------------------------------------

export function DashboardPage() {
  const {
    data: health,
    isLoading: healthLoading,
    isError: healthError,
    refetch: refetchHealth,
    dataUpdatedAt,
  } = useHealth();
  const { data: tools, isLoading: toolsLoading, isError: toolsError } = useTools();
  const { data: sessions, isLoading: sessionsLoading, isError: sessionsError } = useSessions();
  const { data: peers, isLoading: peersLoading, isError: peersError } = usePeers();

  const isHealthy = health?.status === "ok" || health?.status === "healthy";
  const healthStatus: HealthIndicator = health
    ? isHealthy
      ? "healthy"
      : "unhealthy"
    : "unknown";

  const lastUpdated = dataUpdatedAt
    ? new Date(dataUpdatedAt).toLocaleTimeString()
    : null;

  return (
    <div className="space-y-8">
      {/* Page Header */}
      <div className="flex flex-col gap-1 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">Dashboard</h1>
          <p className="text-sm text-muted-foreground">
            Overview of your Forge AI agent instance
          </p>
        </div>
        <div className="flex items-center gap-3">
          {lastUpdated && (
            <span className="flex items-center gap-1.5 text-xs text-muted-foreground">
              <Clock className="h-3 w-3" />
              Updated {lastUpdated}
            </span>
          )}
          <Button
            variant="outline"
            size="sm"
            onClick={() => void refetchHealth()}
          >
            <RefreshCw className="h-4 w-4" />
            Refresh
          </Button>
        </div>
      </div>

      {/* Stats Grid */}
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <StatCard
          title="Health Status"
          value={isHealthy ? "Healthy" : health ? "Degraded" : "--"}
          description={
            health?.uptime
              ? `Uptime: ${formatUptime(health.uptime)}`
              : health?.version
                ? `Version ${health.version}`
                : "Forge gateway status"
          }
          icon={Heart}
          loading={healthLoading}
          error={healthError}
          status={healthStatus}
        />
        <StatCard
          title="Tools"
          value={tools?.length ?? "--"}
          description="Registered tool definitions"
          icon={Wrench}
          loading={toolsLoading}
          error={toolsError}
        />
        <StatCard
          title="Active Sessions"
          value={sessions?.length ?? "--"}
          description="Current agent sessions"
          icon={MessageSquare}
          loading={sessionsLoading}
          error={sessionsError}
        />
        <StatCard
          title="Connected Peers"
          value={peers?.length ?? "--"}
          description="A2A peer agents"
          icon={Network}
          loading={peersLoading}
          error={peersError}
        />
      </div>

      {/* Quick Actions */}
      <div>
        <h2 className="mb-3 text-sm font-semibold text-muted-foreground uppercase tracking-wider">
          Quick Actions
        </h2>
        <div className="grid gap-3 sm:grid-cols-3">
          <QuickAction
            to="/config"
            icon={Settings}
            label="Edit Config"
            description="Update your Forge configuration"
          />
          <QuickAction
            to="/tools"
            icon={Plus}
            label="Add Tool"
            description="Register a new tool definition"
          />
          <QuickAction
            to="/chat"
            icon={MessageSquare}
            label="Open Chat"
            description="Start a new agent conversation"
          />
        </div>
      </div>

      {/* Lower section: two-column layout */}
      <div className="grid gap-6 lg:grid-cols-2">
        {/* System Info */}
        <SystemInfoCard />

        {/* Health Checks Detail */}
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Health Checks</CardTitle>
            <CardDescription>
              Probe endpoints and subsystem status
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            {/* Kubernetes-style probes */}
            <div>
              <p className="mb-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                Probes
              </p>
              <div className="space-y-2">
                <HealthCheckRow label="Liveness" endpoint="/health/live" />
                <HealthCheckRow label="Readiness" endpoint="/health/ready" />
                <HealthCheckRow label="Startup" endpoint="/health/startup" />
              </div>
            </div>

            <Separator />

            {/* Subsystem checks from the ready endpoint */}
            <div>
              <p className="mb-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                Subsystems
              </p>
              <SubsystemChecks />
            </div>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
