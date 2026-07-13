# Starter authorization policy for AgentWeave (packages/forge-security).
#
# AgentWeave's OPAProvider (agentweave/authz/opa.py) POSTs
#   {"input": {caller_spiffe_id, resource_spiffe_id, action, caller_trust_domain,
#              resource_trust_domain, timestamp, context?}}
# to {endpoint}/v1/data/{policy_path}. Its default policy_path is
# "agentweave/authz/allow", i.e. this exact package + the `allow` rule below.
#
# Default-deny: `allow` is false unless a rule below explicitly flips it to true.
# This starter rule only allows same-trust-domain, low-risk actions -- it exists so
# AgentWeave has something real to query end-to-end. The real Forge authorization
# policy (per-tool, per-caller rules) is expected to replace/extend this.
package agentweave.authz

import rego.v1

default allow := false

# Explicit allow: both SPIFFE IDs are present, caller and resource share the same
# SPIFFE trust domain, and the action is on the initial low-risk allowlist.
allow if {
	input.caller_spiffe_id
	input.resource_spiffe_id
	input.caller_trust_domain == input.resource_trust_domain
	input.action in {"read", "list", "health_check"}
}

# Queryable at /v1/data/agentweave/authz/reason -- human-readable justification for
# the decision, useful for audit logging and debugging policy changes.
reason := "allow: same trust domain and action is on the low-risk allowlist" if allow

reason := sprintf(
	"deny: action %q not allowlisted, or caller/resource trust domains differ (caller=%v, resource=%v)",
	[input.action, input.caller_trust_domain, input.resource_trust_domain],
) if not allow
