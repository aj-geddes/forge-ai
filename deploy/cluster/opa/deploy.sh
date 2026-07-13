#!/usr/bin/env bash
# Deploy OPA (Data API server for AgentWeave's OPAProvider) to hvs-k8s.
#
# The policy ConfigMap is generated from policy/agentweave_authz.rego at apply time
# (kubectl create --from-file) so the checked-in .rego file stays the single source
# of truth -- editing the .rego and re-running this script is the update workflow.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
KCTL="${KCTL:-kubectl}"
CONTEXT="${KUBE_CONTEXT:-hvs-k8s}"

"$KCTL" --context "$CONTEXT" apply -f "$SCRIPT_DIR/namespace.yaml"

"$KCTL" --context "$CONTEXT" create configmap opa-policy-agentweave-authz \
  -n opa \
  --from-file="$SCRIPT_DIR/policy/agentweave_authz.rego" \
  --dry-run=client -o yaml \
  | "$KCTL" --context "$CONTEXT" apply -f -

"$KCTL" --context "$CONTEXT" apply -f "$SCRIPT_DIR/deployment.yaml"
"$KCTL" --context "$CONTEXT" apply -f "$SCRIPT_DIR/service.yaml"

"$KCTL" --context "$CONTEXT" rollout status deployment/opa -n opa --timeout=120s
