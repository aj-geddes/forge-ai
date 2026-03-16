import {
  Shield,
  ShieldCheck,
  ShieldOff,
  Gauge,
  Globe,
  Key,
  Loader2,
  Info,
  CheckCircle2,
  AlertTriangle,
} from "lucide-react";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Separator } from "@/components/ui/separator";
import { useConfig } from "@/api/hooks";

// --- Contextual help components ---

function HelpText({ children }: { children: React.ReactNode }) {
  return (
    <p className="flex items-start gap-1.5 text-xs text-muted-foreground mt-1 leading-relaxed">
      <Info className="h-3 w-3 mt-0.5 shrink-0 opacity-60" />
      <span>{children}</span>
    </p>
  );
}

// --- Security posture summary ---

function SecurityPostureBanner({
  security,
}: {
  security?: {
    agentweave?: { enabled: boolean };
    rate_limit?: { requests_per_minute?: number };
    cors_origins?: string[];
    api_keys?: { key_hash: string }[];
    trust_policy?: string;
  };
}) {
  const checks = [
    {
      label: "AgentWeave",
      ok: security?.agentweave?.enabled ?? false,
    },
    {
      label: "Rate limiting",
      ok: security?.rate_limit?.requests_per_minute != null,
    },
    {
      label: "CORS restricted",
      ok:
        (security?.cors_origins?.length ?? 0) > 0 &&
        !security?.cors_origins?.includes("*"),
    },
    {
      label: "API keys",
      ok: (security?.api_keys?.length ?? 0) > 0,
    },
  ];

  const passed = checks.filter((c) => c.ok).length;
  const total = checks.length;
  const allGood = passed === total;

  return (
    <div
      className={`rounded-lg border px-4 py-3 ${
        allGood
          ? "border-green-500/30 bg-green-500/5"
          : "border-yellow-500/30 bg-yellow-500/5"
      }`}
    >
      <div className="flex items-center gap-2 mb-1.5">
        {allGood ? (
          <CheckCircle2 className="h-4 w-4 text-green-600" />
        ) : (
          <AlertTriangle className="h-4 w-4 text-yellow-600" />
        )}
        <span className="text-sm font-medium">
          Security posture: {passed}/{total} controls active
        </span>
      </div>
      <div className="flex flex-wrap gap-2">
        {checks.map((check) => (
          <Badge
            key={check.label}
            variant="outline"
            className={`text-xs ${
              check.ok
                ? "border-green-500/30 text-green-700"
                : "border-muted text-muted-foreground"
            }`}
          >
            {check.ok ? "\u2713" : "\u2013"} {check.label}
          </Badge>
        ))}
      </div>
    </div>
  );
}

// --- Card components ---

function AgentWeaveCard({
  agentweave,
  trustPolicy,
}: {
  agentweave?: {
    enabled: boolean;
    agent_id?: string;
    trust_store?: string;
  };
  trustPolicy?: string;
}) {
  const enabled = agentweave?.enabled ?? false;

  return (
    <Card>
      <CardHeader className="pb-3">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            {enabled ? (
              <ShieldCheck className="h-5 w-5 text-green-600" />
            ) : (
              <ShieldOff className="h-5 w-5 text-muted-foreground" />
            )}
            <CardTitle className="text-lg">AgentWeave</CardTitle>
          </div>
          <Badge
            variant={enabled ? "default" : "secondary"}
            className={
              enabled
                ? "bg-green-500/15 text-green-700 border-green-500/30"
                : ""
            }
          >
            {enabled ? "Enabled" : "Disabled"}
          </Badge>
        </div>
        <CardDescription>
          Identity, signing, audit, and trust framework
        </CardDescription>
        <HelpText>
          AgentWeave is the core security layer for agent-to-agent communication.
          It provides identity verification (callers are who they claim to be),
          message signing (prevents tampering in transit), audit logging (creates
          a compliance trail), and authorization (controls what callers can do).
        </HelpText>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="space-y-2 text-sm">
          {agentweave?.agent_id && (
            <div className="flex items-center justify-between">
              <span className="text-muted-foreground">Agent ID</span>
              <span className="font-mono text-xs">{agentweave.agent_id}</span>
            </div>
          )}
          {trustPolicy && (
            <>
              <div className="flex items-center justify-between">
                <span className="text-muted-foreground">Trust Policy</span>
                <Badge
                  variant="outline"
                  className={
                    trustPolicy === "strict"
                      ? "border-green-500/30 text-green-700"
                      : "border-yellow-500/30 text-yellow-700"
                  }
                >
                  {trustPolicy}
                </Badge>
              </div>
              <HelpText>
                {trustPolicy === "strict"
                  ? "Strict mode requires all callers to present a valid, signed identity before requests are processed."
                  : "Permissive mode allows communication from unverified callers. Use strict mode in production."}
              </HelpText>
            </>
          )}
          {agentweave?.trust_store && (
            <div className="flex items-center justify-between">
              <span className="text-muted-foreground">Trust Store</span>
              <span className="max-w-[200px] truncate font-mono text-xs">
                {agentweave.trust_store}
              </span>
            </div>
          )}
        </div>
        {!enabled && (
          <>
            <Separator />
            <p className="text-xs text-muted-foreground">
              AgentWeave is not configured. Enable it in forge.yaml to activate
              identity verification, message signing, and audit logging.
            </p>
          </>
        )}
      </CardContent>
    </Card>
  );
}

function RateLimitCard({
  rateLimit,
}: {
  rateLimit?: { requests_per_minute?: number; burst?: number };
}) {
  const rpm = rateLimit?.requests_per_minute;
  const burst = rateLimit?.burst;

  return (
    <Card>
      <CardHeader className="pb-3">
        <div className="flex items-center gap-2">
          <Gauge className="h-5 w-5 text-muted-foreground" />
          <CardTitle className="text-lg">Rate Limiting</CardTitle>
        </div>
        <CardDescription>Request throttling configuration</CardDescription>
        <HelpText>
          RPM (requests per minute) is enforced per-caller identity using a
          sliding window algorithm. This protects your agent against abuse,
          runaway automation, and unexpected cost spikes from high-volume callers.
        </HelpText>
      </CardHeader>
      <CardContent className="space-y-3">
        {rpm != null ? (
          <div className="space-y-3">
            <div className="flex items-center justify-between text-sm">
              <span className="text-muted-foreground">Requests per minute</span>
              <span className="font-semibold">{rpm} RPM</span>
            </div>
            <div className="h-2 w-full overflow-hidden rounded-full bg-muted">
              <div
                className="h-full rounded-full bg-primary transition-all"
                style={{ width: `${Math.min((rpm / 1000) * 100, 100)}%` }}
              />
            </div>
            {burst != null && (
              <>
                <div className="flex items-center justify-between text-sm">
                  <span className="text-muted-foreground">Burst allowance</span>
                  <span className="font-semibold">{burst}</span>
                </div>
                <HelpText>
                  Burst allows short spikes above the RPM limit. A burst of {burst} means
                  up to {burst} requests can arrive at once before throttling kicks in.
                </HelpText>
              </>
            )}
          </div>
        ) : (
          <p className="text-sm text-muted-foreground">
            No rate limiting configured. All requests will be accepted without
            throttling.
          </p>
        )}
      </CardContent>
    </Card>
  );
}

function CorsOriginsCard({ origins }: { origins?: string[] }) {
  return (
    <Card>
      <CardHeader className="pb-3">
        <div className="flex items-center gap-2">
          <Globe className="h-5 w-5 text-muted-foreground" />
          <CardTitle className="text-lg">Allowed Origins</CardTitle>
        </div>
        <CardDescription>CORS allowed origins</CardDescription>
        <HelpText>
          These are the web domains allowed to make browser-based requests to
          your agent API. In development, use <code className="font-mono">*</code> to
          allow all origins. In production, list only your actual frontend domains
          to prevent unauthorized cross-site requests.
        </HelpText>
      </CardHeader>
      <CardContent>
        {origins && origins.length > 0 ? (
          <div className="space-y-1.5">
            {origins.map((origin) => (
              <div
                key={origin}
                className="flex items-center gap-2 rounded-md bg-muted/50 px-3 py-1.5 font-mono text-xs"
              >
                {origin}
                {origin === "*" && (
                  <Badge
                    variant="outline"
                    className="border-yellow-500/30 text-yellow-700 text-[10px] ml-auto"
                  >
                    allows all
                  </Badge>
                )}
              </div>
            ))}
          </div>
        ) : (
          <p className="text-sm text-muted-foreground">
            No CORS origins configured. Cross-origin requests may be blocked by
            default browser policy.
          </p>
        )}
      </CardContent>
    </Card>
  );
}

function ApiKeysCard({ apiKeys }: { apiKeys?: { key_hash: string; description?: string; scopes?: string[] }[] }) {
  const count = apiKeys?.length ?? 0;

  return (
    <Card>
      <CardHeader className="pb-3">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Key className="h-5 w-5 text-muted-foreground" />
            <CardTitle className="text-lg">API Keys</CardTitle>
          </div>
          <Badge variant="secondary">{count} configured</Badge>
        </div>
        <CardDescription>Authentication keys (values redacted)</CardDescription>
        <HelpText>
          These are the admin authentication keys that protect the control plane
          API. Values are always redacted for security. Keys can be sourced from
          environment variables or Kubernetes secrets in your forge.yaml.
        </HelpText>
      </CardHeader>
      <CardContent>
        {count > 0 ? (
          <div className="space-y-2">
            {apiKeys?.map((key, idx) => (
              <div
                key={key.key_hash}
                className="flex items-center justify-between rounded-md bg-muted/50 px-3 py-2"
              >
                <div className="space-y-0.5">
                  <p className="text-sm font-medium">
                    {key.description ?? `API Key ${idx + 1}`}
                  </p>
                  <p className="font-mono text-xs text-muted-foreground">
                    {key.key_hash.slice(0, 8)}{"********"}
                  </p>
                </div>
                {key.scopes && key.scopes.length > 0 && (
                  <div className="flex gap-1">
                    {key.scopes.map((scope) => (
                      <Badge key={scope} variant="outline" className="text-xs">
                        {scope}
                      </Badge>
                    ))}
                  </div>
                )}
              </div>
            ))}
          </div>
        ) : (
          <p className="text-sm text-muted-foreground">
            No API keys configured. The gateway may accept unauthenticated
            requests depending on other security settings.
          </p>
        )}
      </CardContent>
    </Card>
  );
}

export function SecurityPage() {
  const { data: config, isLoading, error } = useConfig();

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
            Failed to load configuration: {error.message}
          </p>
        </CardContent>
      </Card>
    );
  }

  const security = config?.security;

  return (
    <div className="space-y-6">
      <div className="flex items-center gap-3">
        <Shield className="h-8 w-8 text-muted-foreground" />
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Security</h1>
          <p className="text-sm text-muted-foreground">
            Current security posture of your agent &mdash; authentication, rate
            limiting, CORS, and trust framework status. These settings are
            read from your forge.yaml configuration.
          </p>
        </div>
      </div>

      <SecurityPostureBanner security={security} />

      <div className="grid gap-4 md:grid-cols-2">
        <AgentWeaveCard
          agentweave={security?.agentweave}
          trustPolicy={security?.trust_policy}
        />
        <RateLimitCard rateLimit={security?.rate_limit} />
        <CorsOriginsCard origins={security?.cors_origins} />
        <ApiKeysCard apiKeys={security?.api_keys} />
      </div>
    </div>
  );
}
