// Mirrors forge_gateway/models.py TokenListItem / MintTokenResponse and the
// request body forge_gateway/routes/tokens.py's MintTokenRequest accepts
// (ADR-0002 SS5). GET /v1/auth/tokens responds with an envelope object
// (`{tokens: [...]}` -- TokenListResponse), never a bare array.

export interface ApiKeySummary {
  id: string;
  label: string;
  roles: string[];
  created_at: string;
  expires_at: string;
  revoked_at: string | null;
}

/**
 * The POST /v1/auth/tokens response. `token` is the raw `forge_sk_...`
 * secret -- it is returned in this shape exactly once and is never present
 * in `ApiKeySummary` (the list/GET shape).
 */
export interface MintedApiKey extends ApiKeySummary {
  token: string;
}

export interface CreateApiKeyRequest {
  label: string;
  /** Omitted -- backend defaults to the caller's own current roles. */
  roles?: string[];
  /** Omitted -- backend defaults to `default_ttl_seconds` (30 days). */
  ttl_seconds?: number;
}
