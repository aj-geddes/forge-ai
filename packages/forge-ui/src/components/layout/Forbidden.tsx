import { ShieldAlert } from "lucide-react";
import {
  Card,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";

/**
 * Rendered when the caller is authenticated but lacks the permission for
 * the current page (HTTP 403). This is deliberately NOT a redirect to
 * login -- the user is signed in, they simply may not do this. Bouncing an
 * authenticated-but-unauthorized user back into the login flow is the
 * "infuriating" loop the gateway contract explicitly calls out.
 */
export function Forbidden() {
  return (
    <div className="flex min-h-[400px] items-center justify-center p-6">
      <Card className="w-full max-w-md">
        <CardHeader className="text-center">
          <div className="mx-auto mb-2 flex h-12 w-12 items-center justify-center rounded-full bg-destructive/10">
            <ShieldAlert className="h-6 w-6 text-destructive" />
          </div>
          <CardTitle className="text-lg">You don&apos;t have permission</CardTitle>
          <CardDescription>
            Your account is signed in, but doesn&apos;t have the role needed to
            view this page. Ask an administrator to grant you access.
          </CardDescription>
        </CardHeader>
      </Card>
    </div>
  );
}
