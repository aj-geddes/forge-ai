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
import type { SecurityConfig } from "@/types/config";

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
//
// These four checks read the *actual* backend SecurityConfig shape
// (forge_config/schema.py SecurityConfig): agentweave.enabled,
// rate_limit_rpm, allowed_origins, and api_keys.{enabled,keys}. The
// previous version of this page read a shape (cors_origins, rate_limit,
// trust_policy at the top level, api_keys as a list of {key_hash}) that the
// backend never returns, so 3 of 4 checks could never pass.

function SecurityPostureBanner({ security }: { security?: SecurityConfig }) {
  const checks = [
    {
      label: "AgentWeave",
      ok: security?.agentweave?.enabled ?? false,
    },
    {
      label: "Rate limiting",
      ok: (security?.rate_limit_rpm ?? 0) > 0,
    },
    {
      label: "CORS restricted",
      ok:
        (security?.allowed_origins?.length ?? 0) > 0 &&
        !security?.allowed_origins?.includes("*"),
    },
    {
      label: "API keys",
      ok: (security?.api_keys?.enabled ?? false) && (security?.api_keys?.keys.length ?? 0) > 0,
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
            {check.ok ? "✓" : "–"} {check.label}
          </Badge>
        ))}
      </div>
    </div>
  );
}

// --- Card components ---

function AgentWeaveCard({ agentweave }: { agentweave?: SecurityConfig["agentweave"] }) {
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
          {agentweave?.trust_domain && (
            <div className="flex items-center justify-between">
              <span className="text-muted-foreground">Trust Domain</span>
              <span className="font-mono text-xs">{agentweave.trust_domain}</span>
            </div>
          )}
          {agentweave?.trust_policy && (
            <>
              <div className="flex items-center justify-between">
                <span className="text-muted-foreground">Trust Policy</span>
                <Badge
                  variant="outline"
                  className={
                    agentweave.trust_policy === "strict"
                      ? "border-green-500/30 text-green-700"
                      : "border-yellow-500/30 text-yellow-700"
                  }
                >
                  {agentweave.trust_policy}
                </Badge>
              </div>
              <HelpText>
                {agentweave.trust_policy === "strict"
                  ? "Strict mode requires all callers to present a valid, signed identity before requests are processed."
                  : "Permissive mode allows communication from unverified callers. Use strict mode in production."}
              </HelpText>
            </>
          )}
          {agentweave?.authz_provider && (
            <div className="flex items-center justify-between">
              <span className="text-muted-foreground">Authorization Provider</span>
              <span className="max-w-[200px] truncate font-mono text-xs">
                {agentweave.authz_provider}
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

function RateLimitCard({ rateLimitRpm }: { rateLimitRpm?: number }) {
  const rpm = rateLimitRpm;

  return (
    <Card>
      <CardHeader className="pb-3">
        <div className="flex items-center gap-2">
          <Gauge className="h-5 w-5 text-muted-foreground" />
          <CardTitle className="text-lg">Rate Limiting</CardTitle>
        </div>
        <CardDescription>Request throttling configuration</CardDescription>
        <HelpText>
          RPM (requests per minute) is enforced per-caller identity. This protects
          your agent against abuse, runaway automation, and unexpected cost spikes
          from high-volume callers.
        </HelpText>
      </CardHeader>
      <CardContent className="space-y-3">
        {rpm != null && rpm > 0 ? (
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

function AllowedOriginsCard({ origins }: { origins?: string[] }) {
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
          your agent API. List an explicit allowlist of your frontend domains &mdash;{" "}
          <code className="font-mono">*</code> is rejected by validation whenever
          credentialed sessions (OIDC) are enabled, since a wildcard origin combined
          with credentials is a security risk.
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

function ApiKeysCard({ apiKeys }: { apiKeys?: SecurityConfig["api_keys"] }) {
  const keys = apiKeys?.keys ?? [];
  const count = keys.length;

  return (
    <Card>
      <CardHeader className="pb-3">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Key className="h-5 w-5 text-muted-foreground" />
            <CardTitle className="text-lg">API Keys</CardTitle>
          </div>
          <Badge variant="secondary">
            {apiKeys?.enabled ? `${count} configured` : "disabled"}
          </Badge>
        </div>
        <CardDescription>Authentication keys (values redacted)</CardDescription>
        <HelpText>
          These are the admin authentication keys that protect the control plane
          API. Values are always redacted for security. Keys can be sourced from
          environment variables or Kubernetes secrets in your forge.yaml.
        </HelpText>
      </CardHeader>
      <CardContent>
        {apiKeys?.enabled && count > 0 ? (
          <div className="space-y-2">
            {keys.map((key, idx) => (
              <div
                key={`${key.source}-${key.name}-${idx}`}
                className="flex items-center justify-between rounded-md bg-muted/50 px-3 py-2"
              >
                <div className="space-y-0.5">
                  <p className="text-sm font-medium">API Key {idx + 1}</p>
                  <p className="font-mono text-xs text-muted-foreground">{key.name}</p>
                </div>
                <Badge variant="outline" className="text-xs">
                  {key.source}
                </Badge>
              </div>
            ))}
          </div>
        ) : (
          <p className="text-sm text-muted-foreground">
            No legacy API keys configured. <code className="font-mono">api_keys</code> is a
            deprecated, optional control &mdash; request authentication is enforced
            independently via OIDC (browser sessions) and service tokens, so the gateway
            does not accept unauthenticated requests.
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
        <AgentWeaveCard agentweave={security?.agentweave} />
        <RateLimitCard rateLimitRpm={security?.rate_limit_rpm} />
        <AllowedOriginsCard origins={security?.allowed_origins} />
        <ApiKeysCard apiKeys={security?.api_keys} />
      </div>
    </div>
  );
}
