// Mirrors GET /v1/auth/me (forge_gateway/routes/auth.py, ADR-0001 section 6.4).
// The gateway is the sole source of truth for identity and authorization --
// the UI never derives permissions itself, only reads and renders them.

export type PrincipalKind = "user" | "service" | "dev";

export interface Principal {
  kind: PrincipalKind;
  sub: string;
  email?: string | null;
  name?: string | null;
  groups: string[];
  roles: string[];
  permissions: string[];
}
