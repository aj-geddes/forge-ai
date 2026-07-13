import { useState } from "react";
import {
  KeyRound,
  Plus,
  Loader2,
  Copy,
  Check,
  Info,
  ShieldAlert,
} from "lucide-react";
import { Card, CardContent } from "@/components/ui/card";
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
import { useAuth } from "@/api/auth";
import { ApiError } from "@/api/client";
import { useApiKeys, useCreateApiKey, useRevokeApiKey } from "@/api/apiKeys";
import type { ApiKeySummary, MintedApiKey } from "@/types/apiKeys";

// Self-service API keys (ADR-0002 SS9). BFF mode -- this page only ever
// talks to the gateway via the session cookie (api/client.ts); it never
// holds a bearer token. The one exception is the freshly minted secret
// itself, which is handled specially: it lives only in this page's React
// state (MintedTokenDialog below) and is dropped the moment the user
// dismisses that dialog. It is never written to localStorage,
// sessionStorage, or the TanStack Query cache.

function HelpText({ children }: { children: React.ReactNode }) {
  return (
    <p className="flex items-start gap-1.5 text-xs text-muted-foreground mt-1 leading-relaxed">
      <Info className="h-3 w-3 mt-0.5 shrink-0 opacity-60" />
      <span>{children}</span>
    </p>
  );
}

const TTL_PRESETS = [
  { label: "1 hour", seconds: 3_600 },
  { label: "1 day", seconds: 86_400 },
  { label: "7 days", seconds: 604_800 },
  { label: "30 days (default)", seconds: 2_592_000 },
  { label: "90 days", seconds: 7_776_000 },
] as const;

const DEFAULT_TTL_SECONDS = 2_592_000;

function formatDate(iso: string): string {
  const parsed = new Date(iso);
  return Number.isNaN(parsed.getTime()) ? iso : parsed.toLocaleString();
}

function isExpired(iso: string): boolean {
  return new Date(iso).getTime() < Date.now();
}

/**
 * Backend errors arrive as `ApiError` with `.message` set to the raw
 * `{error: "..."}` code (e.g. `escalation_denied`, `ttl_out_of_range`) --
 * client.ts already extracts it, so surfacing it is just reading `.message`.
 */
function errorMessage(error: unknown): string {
  return error instanceof ApiError ? error.message : "Something went wrong";
}

// --- Status badge ---

function StatusBadge({ apiKey }: { apiKey: ApiKeySummary }) {
  if (apiKey.revoked_at) {
    return <Badge variant="destructive">Revoked</Badge>;
  }
  if (isExpired(apiKey.expires_at)) {
    return <Badge variant="secondary">Expired</Badge>;
  }
  return (
    <Badge
      variant="outline"
      className="bg-green-500/15 text-green-700 border-green-500/30"
    >
      Active
    </Badge>
  );
}

// --- Per-row revoke control ---

function RevokeAction({ apiKey }: { apiKey: ApiKeySummary }) {
  const [confirming, setConfirming] = useState(false);
  const revokeMutation = useRevokeApiKey();

  if (apiKey.revoked_at) {
    return <span className="text-xs text-muted-foreground">--</span>;
  }

  if (confirming) {
    return (
      <div className="flex flex-col items-end gap-1">
        <div className="flex items-center justify-end gap-1.5">
          <Button
            size="sm"
            variant="destructive"
            disabled={revokeMutation.isPending}
            onClick={() =>
              revokeMutation.mutate(apiKey.id, {
                onSuccess: () => setConfirming(false),
              })
            }
          >
            {revokeMutation.isPending ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : (
              "Confirm"
            )}
          </Button>
          <Button
            size="sm"
            variant="outline"
            disabled={revokeMutation.isPending}
            onClick={() => setConfirming(false)}
          >
            Cancel
          </Button>
        </div>
        {revokeMutation.isError && (
          <span className="text-xs text-destructive">
            {errorMessage(revokeMutation.error)}
          </span>
        )}
      </div>
    );
  }

  return (
    <Button size="sm" variant="outline" onClick={() => setConfirming(true)}>
      Revoke
    </Button>
  );
}

// --- Table ---

function ApiKeysTable({ apiKeys }: { apiKeys: ApiKeySummary[] }) {
  return (
    <Card>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b text-left text-xs uppercase tracking-wider text-muted-foreground">
              <th className="px-4 py-3 font-medium">Label</th>
              <th className="px-4 py-3 font-medium">Roles</th>
              <th className="px-4 py-3 font-medium">Created</th>
              <th className="px-4 py-3 font-medium">Expires</th>
              <th className="px-4 py-3 font-medium">Status</th>
              <th className="px-4 py-3 font-medium text-right">Actions</th>
            </tr>
          </thead>
          <tbody>
            {apiKeys.map((apiKey) => (
              <tr
                key={apiKey.id}
                className="border-b last:border-b-0 hover:bg-muted/50"
              >
                <td className="px-4 py-3 font-medium">{apiKey.label}</td>
                <td className="px-4 py-3">
                  <div className="flex flex-wrap gap-1">
                    {apiKey.roles.map((role) => (
                      <Badge key={role} variant="outline" className="text-xs">
                        {role}
                      </Badge>
                    ))}
                  </div>
                </td>
                <td className="px-4 py-3 text-muted-foreground">
                  {formatDate(apiKey.created_at)}
                </td>
                <td className="px-4 py-3 text-muted-foreground">
                  {formatDate(apiKey.expires_at)}
                </td>
                <td className="px-4 py-3">
                  <StatusBadge apiKey={apiKey} />
                </td>
                <td className="px-4 py-3 text-right">
                  <RevokeAction apiKey={apiKey} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  );
}

// --- Create dialog ---

function CreateApiKeyDialog({
  ownRoles,
  onCreated,
}: {
  ownRoles: string[];
  onCreated: (minted: MintedApiKey) => void;
}) {
  const [open, setOpen] = useState(false);
  const [label, setLabel] = useState("");
  const [selectedRoles, setSelectedRoles] = useState<string[]>([]);
  const [ttlSeconds, setTtlSeconds] = useState<number>(DEFAULT_TTL_SECONDS);
  const createMutation = useCreateApiKey();

  const resetForm = () => {
    setLabel("");
    setSelectedRoles([]);
    setTtlSeconds(DEFAULT_TTL_SECONDS);
    createMutation.reset();
  };

  const handleOpenChange = (next: boolean) => {
    setOpen(next);
    if (!next) resetForm();
  };

  const toggleRole = (role: string) => {
    setSelectedRoles((prev) =>
      prev.includes(role) ? prev.filter((r) => r !== role) : [...prev, role],
    );
  };

  const handleSubmit = () => {
    const trimmedLabel = label.trim();
    if (!trimmedLabel) return;

    createMutation.mutate(
      {
        label: trimmedLabel,
        roles: selectedRoles.length > 0 ? selectedRoles : undefined,
        ttl_seconds: ttlSeconds,
      },
      {
        onSuccess: (minted) => {
          onCreated(minted);
          setOpen(false);
          resetForm();
        },
      },
    );
  };

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogTrigger className="inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground transition-colors hover:bg-primary/90">
        <Plus className="h-4 w-4" />
        Create API key
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Create API Key</DialogTitle>
          <DialogDescription>
            Mint a new self-service key to call the API without a browser
            session. The key is scoped to your own roles -- you cannot grant
            it permissions you do not already have.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-4 py-4">
          <div className="space-y-2">
            <Label htmlFor="apikey-label">Label</Label>
            <Input
              id="apikey-label"
              placeholder="laptop CLI"
              value={label}
              onChange={(e) => setLabel(e.target.value)}
              disabled={createMutation.isPending}
            />
            <HelpText>
              A short, human-readable name so you can recognize this key
              later (1-64 characters).
            </HelpText>
          </div>

          <div className="space-y-2">
            <Label>Roles (optional)</Label>
            {ownRoles.length > 0 ? (
              <div className="space-y-1.5">
                {ownRoles.map((role) => (
                  <label
                    key={role}
                    htmlFor={`apikey-role-${role}`}
                    className="flex items-center gap-2 text-sm"
                  >
                    <input
                      id={`apikey-role-${role}`}
                      type="checkbox"
                      checked={selectedRoles.includes(role)}
                      onChange={() => toggleRole(role)}
                      disabled={createMutation.isPending}
                      className="h-4 w-4 rounded border-input"
                    />
                    {role}
                  </label>
                ))}
              </div>
            ) : (
              <p className="text-sm text-muted-foreground">
                You have no roles to grant.
              </p>
            )}
            <HelpText>
              Leave unchecked to mint a key with all of your current roles.
              You can only select from roles you already hold -- the server
              refuses any request for more ({'"'}escalation_denied{'"'}).
            </HelpText>
          </div>

          <div className="space-y-2">
            <Label htmlFor="apikey-ttl">Expires in</Label>
            <Select
              id="apikey-ttl"
              value={String(ttlSeconds)}
              onChange={(e) => setTtlSeconds(Number(e.target.value))}
              disabled={createMutation.isPending}
            >
              {TTL_PRESETS.map((preset) => (
                <option key={preset.seconds} value={preset.seconds}>
                  {preset.label}
                </option>
              ))}
            </Select>
            <HelpText>
              How long the key stays valid. It can be revoked earlier at any
              time from this page.
            </HelpText>
          </div>

          {createMutation.isError && (
            <p className="text-sm text-destructive">
              {errorMessage(createMutation.error)}
            </p>
          )}
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => handleOpenChange(false)}>
            Cancel
          </Button>
          <Button
            onClick={handleSubmit}
            disabled={!label.trim() || createMutation.isPending}
          >
            {createMutation.isPending ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : null}
            Create key
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// --- Once-only minted token dialog ---

function MintedTokenDialog({
  minted,
  onDismiss,
}: {
  minted: MintedApiKey | null;
  onDismiss: () => void;
}) {
  const [copied, setCopied] = useState(false);

  if (!minted) return null;

  const handleCopy = () => {
    navigator.clipboard
      .writeText(minted.token)
      .then(() => {
        setCopied(true);
        setTimeout(() => setCopied(false), 2000);
      })
      .catch(() => {
        // Clipboard API unavailable -- the token is still selectable/
        // copyable by hand from the code block below.
      });
  };

  return (
    <Dialog open onOpenChange={(next) => !next && onDismiss()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <ShieldAlert className="h-5 w-5 text-yellow-600" />
            API key created
          </DialogTitle>
          <DialogDescription>
            Copy this key now. For your security, it will not be shown again
            -- if you lose it, revoke it and mint a new one.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-3 py-2">
          <div className="rounded-lg border border-yellow-500/30 bg-yellow-500/5 px-3 py-2 text-xs text-muted-foreground">
            This is the only time the raw key is displayed. It is not stored
            anywhere -- not in this browser, not by the server -- beyond
            this dialog.
          </div>
          <div className="flex items-center gap-2">
            <code
              data-testid="minted-token-value"
              className="flex-1 overflow-x-auto rounded-md bg-muted px-3 py-2 font-mono text-xs"
            >
              {minted.token}
            </code>
            <Button
              type="button"
              variant="outline"
              size="icon"
              onClick={handleCopy}
              title="Copy to clipboard"
            >
              {copied ? (
                <Check className="h-4 w-4" />
              ) : (
                <Copy className="h-4 w-4" />
              )}
            </Button>
          </div>
        </div>
        <DialogFooter>
          <Button onClick={onDismiss}>Done, I&apos;ve saved my key</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// --- Page ---

export function ApiKeysPage() {
  const { user } = useAuth();
  const { data: apiKeys, isLoading, error } = useApiKeys();
  const [mintedKey, setMintedKey] = useState<MintedApiKey | null>(null);

  const ownRoles = user?.roles ?? [];
  const featureDisabled = error instanceof ApiError && error.status === 404;

  if (isLoading) {
    return (
      <div className="flex min-h-[400px] items-center justify-center">
        <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
      </div>
    );
  }

  if (featureDisabled) {
    return (
      <Card>
        <CardContent className="py-12">
          <div className="flex flex-col items-center gap-3 text-center">
            <KeyRound className="h-12 w-12 text-muted-foreground/50" />
            <div className="max-w-md">
              <p className="text-lg font-medium text-muted-foreground">
                API keys are not enabled
              </p>
              <p className="mt-1 text-sm text-muted-foreground">
                Self-service API keys are disabled on this Forge instance.
                Ask an administrator to enable{" "}
                <code className="font-mono">
                  security.service_tokens.user_tokens.enabled
                </code>{" "}
                in forge.yaml.
              </p>
            </div>
          </div>
        </CardContent>
      </Card>
    );
  }

  if (error) {
    return (
      <Card>
        <CardContent className="py-8">
          <p className="text-center text-sm text-destructive">
            Failed to load API keys: {errorMessage(error)}
          </p>
        </CardContent>
      </Card>
    );
  }

  const keys = apiKeys ?? [];

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <KeyRound className="h-8 w-8 text-muted-foreground" />
          <div>
            <h1 className="text-2xl font-semibold tracking-tight">
              API Keys
            </h1>
            <p className="text-sm text-muted-foreground max-w-xl">
              Mint your own keys to call the Forge API without a browser
              session -- for CLIs, scripts, or other agents. Each key is
              scoped to a subset of your current roles.
            </p>
          </div>
        </div>
        <CreateApiKeyDialog ownRoles={ownRoles} onCreated={setMintedKey} />
      </div>

      {keys.length === 0 ? (
        <Card>
          <CardContent className="py-12">
            <div className="flex flex-col items-center gap-3 text-center">
              <KeyRound className="h-12 w-12 text-muted-foreground/50" />
              <div className="max-w-md">
                <p className="text-lg font-medium text-muted-foreground">
                  No API keys yet
                </p>
                <p className="mt-1 text-sm text-muted-foreground">
                  Create your first key to authenticate CLI or script calls
                  to the Forge API.
                </p>
              </div>
            </div>
          </CardContent>
        </Card>
      ) : (
        <ApiKeysTable apiKeys={keys} />
      )}

      <MintedTokenDialog
        minted={mintedKey}
        onDismiss={() => setMintedKey(null)}
      />
    </div>
  );
}
