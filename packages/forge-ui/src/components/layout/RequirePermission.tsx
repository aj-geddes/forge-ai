import type { ReactNode } from "react";
import { useAuth } from "@/api/auth";
import { Forbidden } from "./Forbidden";

/**
 * Cosmetic, client-side gate for admin-ish pages (config write, tool
 * management, peers). The gateway is the authority -- every request this
 * page makes is independently enforced there -- but hiding a control (or a
 * whole page) the caller cannot use is better UX than letting them click
 * into a wall of 403s. Renders `Forbidden`, never a login redirect: the
 * caller IS authenticated, they simply lack the permission.
 */
export function RequirePermission({
  permission,
  children,
}: {
  permission: string;
  children: ReactNode;
}) {
  const { can, isLoading } = useAuth();

  if (isLoading) return null;
  if (!can(permission)) return <Forbidden />;
  return <>{children}</>;
}
