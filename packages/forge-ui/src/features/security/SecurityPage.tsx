import {
  Shield,
  ShieldCheck,
  ShieldOff,
  Gauge,
  Globe,
  Key,
  Loader2,
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
              <div className="flex items-center justify-between text-sm">
                <span className="text-muted-foreground">Burst allowance</span>
                <span className="font-semibold">{burst}</span>
              </div>
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
            AgentWeave security and access control
          </p>
        </div>
      </div>

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
