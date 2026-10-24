#!/usr/bin/env bash
# The in-cluster half of `make down`, in the order that leaves nothing billable behind:
#
#   1. helmfile -l tier=app destroy     consoletrack (its Gateway, so the Load Balancer Controller
#                                       deletes the ALB) and demo-app
#   2. delete any Gateway left over     then wait for the load balancers to go
#   3. scripts/dns-clean.sh             ExternalDNS (still running) deletes DOMAIN's records once the
#                                       Gateway is gone; whatever it leaves is deleted here, so the
#                                       next cluster starts with no stale records (needs DNS_ZONE_ID)
#   3b. helmfile -l tier=addons destroy the controller goes only after the ALB it manages
#   4. delete the PersistentVolumeClaims that remain (operators leave theirs behind), so the EBS
#      CSI driver, which the cluster still runs, deletes the volumes
#   5. wait for project=consoletrack load balancers and ELB ENIs to be gone
#
# Safe to rerun, and skips cleanly when the cluster is already gone. A human runs it through
# `make down`; it reads KUBE_CONTEXT, HELMFILE, and AWS_REGION from the environment.
set -euo pipefail

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
context=${KUBE_CONTEXT:-consoletrack-eks}
helmfile=${HELMFILE:-helmfile}
kubectl=(kubectl --context "$context")
hf=("$helmfile" --file "$root/helm/helmfile.yaml.gotmpl" -e eks --kube-context "$context")

# dns_clean <seconds ExternalDNS gets>: a failure is loud but doesn't stop the teardown, since the
# records cost nothing; `make dns-clean` repeats it.
dns_clean() {
	[[ -n "${DOMAIN:-}" ]] || return 0
	DNS_CLEAN_WAIT=$1 "$root/scripts/dns-clean.sh" ||
		echo "down-cluster: WARNING: could not clean DOMAIN's DNS records; run make dns-clean before the next make up" >&2
}

if ! "${kubectl[@]}" get --raw /readyz >/dev/null 2>&1; then
	echo "down-cluster: context $context is unreachable (no cluster, or a stale kubeconfig); skipped the Helmfile steps"
	dns_clean 0
	exit 0
fi

echo "down-cluster: destroying the app releases (consoletrack, demo-app)"
"${hf[@]}" -l tier=app destroy

if "${kubectl[@]}" get crd gateways.gateway.networking.k8s.io >/dev/null 2>&1; then
	echo "down-cluster: deleting any Gateway left over"
	"${kubectl[@]}" delete gateways.gateway.networking.k8s.io --all --all-namespaces --wait --timeout=5m --ignore-not-found
fi
"$root/scripts/teardown-verify.sh" wait

dns_clean 120

echo "down-cluster: destroying the add-ons"
"${hf[@]}" -l tier=addons destroy

echo "down-cluster: deleting the PersistentVolumeClaims that remain"
for ns in consoletrack demo monitoring cnpg-system; do
	"${kubectl[@]}" -n "$ns" delete pvc --all --wait --timeout=5m --ignore-not-found 2>/dev/null || true
done

"$root/scripts/teardown-verify.sh" wait
