import { Github, Flame } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";

/**
 * Builds the gateway's OIDC entry point, preserving the current path as
 * `next` so `GET /auth/callback` can return the user where they were
 * (ADR-0001 section 9.1). `next` is only ever a same-app relative path --
 * the gateway independently validates it before honoring it.
 */
function buildLoginUrl(): string {
  if (typeof window === "undefined") return "/auth/login";
  const next = `${window.location.pathname}${window.location.search}`;
  return next && next !== "/"
    ? `/auth/login?next=${encodeURIComponent(next)}`
    : "/auth/login";
}

export function LoginPage() {
  const handleSignIn = () => {
    // Full browser navigation, never fetch/XHR: the browser must follow the
    // 302 to Dex itself, which an XHR-based request cannot do.
    window.location.assign(buildLoginUrl());
  };

  return (
    <div className="flex min-h-screen items-center justify-center bg-background p-4">
      <Card className="w-full max-w-sm">
        <CardHeader className="text-center">
          <div className="mx-auto mb-2 flex h-12 w-12 items-center justify-center rounded-full bg-primary/10">
            <Flame className="h-6 w-6 text-primary" />
          </div>
          <CardTitle className="text-xl">Forge AI</CardTitle>
          <CardDescription>
            Sign in with your GitHub account to access the control plane.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <Button className="w-full" onClick={handleSignIn}>
            <Github className="h-4 w-4" />
            Sign in with GitHub
          </Button>
          <p className="text-center text-xs text-muted-foreground">
            Authentication is handled by the platform single sign-on (Dex).
            Forge never sees or stores your credentials, and signing out of
            Forge does not sign you out of your platform SSO session.
          </p>
        </CardContent>
      </Card>
    </div>
  );
}
