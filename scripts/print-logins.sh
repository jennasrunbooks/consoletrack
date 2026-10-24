#!/usr/bin/env bash
# One block with everything needed to get in, printed as the last output of `make up` (and on
# demand by `make logins`): where each UI is, and its username and password.
#
#   ConsoleTrack  the URL; admin's password only if this run generated it (LOGIN_BANNER_FILE, written
#                 by create-secrets.sh): the Secret holds just a hash, so a later run can't show it
#   Grafana       the chart-generated admin password, read from its Secret in the monitoring namespace
#
# Reads EXPOSURE, DOMAIN, OPEN_PORT, KUBE_CONTEXT, and LOGIN_BANNER_FILE from the environment (the
# Makefile exports them). Humans only: it prints a password to the terminal.
set -euo pipefail

context=${KUBE_CONTEXT:-consoletrack-eks}
port=${OPEN_PORT:-8080}
kubectl=(kubectl --context "$context")

line() {
	printf '  %s\n' "$*"
}

echo
echo "================================================================"
echo "  LOGINS"
echo "================================================================"
echo
echo "  ConsoleTrack"
if [[ "${EXPOSURE:-}" == alb ]]; then
	line "  url:       https://${DOMAIN} (from ALLOWED_CIDRS only)"
else
	line "  url:       http://127.0.0.1:$port after: make open"
fi
line "  username:  admin"
password=
if [[ -n "${LOGIN_BANNER_FILE:-}" && -f "$LOGIN_BANNER_FILE" ]]; then
	password=$(sed -nE 's/^ *password: +//p' "$LOGIN_BANNER_FILE" | head -n1)
fi
if [[ -n "$password" ]]; then
	line "  password:  $password"
	line "             (generated now, shown once; only its hash is stored. Save it.)"
else
	line "  password:  not shown again (only its hash is stored). Yours from .env"
	line "             (ADMIN_PASSWORD_HASH), or from the first make up. To reset: delete the"
	line "             consoletrack-auth Secret, make create-secrets, then"
	line "             kubectl rollout restart deploy/backend -n consoletrack"
fi
line "  viewer:    only if VIEWER_PASSWORD_HASH was set in .env (username viewer)"
echo
echo "  Grafana"
line "  url:       http://127.0.0.1:3000 after:"
line "             kubectl --context $context -n monitoring port-forward svc/kube-prometheus-stack-grafana 3000:80"
line "  username:  admin"
if grafana=$("${kubectl[@]}" -n monitoring get secret kube-prometheus-stack-grafana \
	-o jsonpath='{.data.admin-password}' 2>/dev/null) && [[ -n "$grafana" ]]; then
	line "  password:  $(base64 -d <<<"$grafana")"
else
	line "  password:  (could not read the kube-prometheus-stack-grafana Secret in monitoring)"
fi
echo
echo "================================================================"
