#!/bin/sh
# Grafana's entrypoint for `make local` (docker-compose.yaml → grafana): never admin/admin.
# GRAFANA_ADMIN_PASSWORD from .env or the shell wins. Otherwise a random password is generated
# once and kept in the grafana-data volume, where `make local` reads it to print it.
# Grafana applies the password when it creates its database, so a new GRAFANA_ADMIN_PASSWORD
# takes effect after `make local-down` (which removes the volume).
set -eu
file=/var/lib/grafana/admin-password
if [ -n "${GRAFANA_ADMIN_PASSWORD:-}" ]; then
  GF_SECURITY_ADMIN_PASSWORD=$GRAFANA_ADMIN_PASSWORD
  rm -f "$file"
else
  if [ ! -s "$file" ]; then
    (umask 077 && head -c 18 /dev/urandom | base64 | tr -d '/+=\n' > "$file")
  fi
  GF_SECURITY_ADMIN_PASSWORD=$(cat "$file")
fi
export GF_SECURITY_ADMIN_PASSWORD
unset GRAFANA_ADMIN_PASSWORD
exec /run.sh "$@"
