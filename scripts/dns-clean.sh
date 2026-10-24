#!/usr/bin/env bash
# Removes the Route 53 records ExternalDNS left for DOMAIN, so the next cluster's ExternalDNS can
# create them. Records from a deleted cluster that have no ownership TXT are ones ExternalDNS's
# TXT registry refuses to touch, so the name never resolves to the new ALB (see README → DNS
# after make up).
#
#   dns-clean.sh clean   (default) wait up to DNS_CLEAN_WAIT seconds for ExternalDNS to delete
#                        the records itself, then delete what is left
#   dns-clean.sh list    print the records clean would delete; exit 1 if there are any
#
# Deletes only, in the zone DNS_ZONE_ID (from the global root's zone_id output, never a name
# lookup), and only:
#   - A and AAAA records named exactly DOMAIN
#   - TXT ownership records named DOMAIN, a-DOMAIN, or aaaa-DOMAIN whose value says
#     heritage=external-dns and external-dns/owner=NAME_SUFFIX
# NS, SOA, the ACM validation CNAME, and every other name or owner are never touched.
#
# A human runs it through `make dns-clean`; `make down` runs it with DNS_CLEAN_WAIT=120 before the
# add-ons (ExternalDNS) go. Reads DOMAIN, NAME_SUFFIX, and DNS_ZONE_ID from the environment
# (the Makefile exports them), plus DNS_CLEAN_WAIT (default 0) and DNS_CLEAN_INTERVAL (default 10).
# Output masks any 12-digit number (account IDs).
set -euo pipefail

mode=${1:-clean}

die() {
	echo "dns-clean: $*" >&2
	exit 1
}

mask() {
	sed -E 's/[0-9]{12}/***/g'
}

for tool in aws jq; do
	command -v "$tool" >/dev/null || die "needs $tool"
done

domain=${DOMAIN:-}
[[ -n "$domain" ]] || {
	echo "dns-clean: DOMAIN is empty; nothing to clean"
	exit 0
}
domain=$(tr '[:upper:]' '[:lower:]' <<<"${domain%.}")
owner=${NAME_SUFFIX:?set NAME_SUFFIX}
zone=${DNS_ZONE_ID:-}
[[ "$zone" =~ ^Z[A-Z0-9]+$ ]] ||
	die "no Route 53 zone ID (the global root's zone_id output); apply consoletrack/global first"

# The records to delete, as a JSON array of resource record sets.
matching() {
	aws route53 list-resource-record-sets --hosted-zone-id "$zone" --output json | jq -c \
		--arg d "$domain." --arg a "a-$domain." --arg aaaa "aaaa-$domain." --arg owner "$owner" '
		def owned: any(.ResourceRecords[]?.Value;
			test("heritage=external-dns,external-dns/owner=" + ($owner | gsub("(?<c>[^A-Za-z0-9])"; "\\" + .c)) + "(,|\"|$)"));
		[.ResourceRecordSets[]
			| select(
				(.Name == $d and (.Type == "A" or .Type == "AAAA"))
				or ((.Name == $d or .Name == $a or .Name == $aaaa) and .Type == "TXT" and owned))]'
}

# One line per record: "<type> <name>".
describe() {
	jq -r '.[] | "\(.Type) \(.Name | rtrimstr("."))"' <<<"$1" | mask
}

# True while ExternalDNS's ownership records are there: it deletes them in the same change as the
# A and AAAA records, so once they are gone it has finished. A record without them is one it won't
# touch, and waiting for it would only run out the clock.
owned_txt_left() {
	[[ -n "$(jq -r '.[] | select(.Type == "TXT") | .Name' <<<"$1")" ]]
}

records=$(matching)

case "$mode" in
list)
	if [[ "$(jq length <<<"$records")" -gt 0 ]]; then
		describe "$records"
		exit 1
	fi
	;;
clean)
	wait=${DNS_CLEAN_WAIT:-0}
	interval=${DNS_CLEAN_INTERVAL:-10}
	deadline=$((SECONDS + wait))
	while owned_txt_left "$records" && ((SECONDS < deadline)); do
		echo "dns-clean: waiting for ExternalDNS to delete the records for $domain..."
		sleep "$interval"
		records=$(matching)
	done
	if [[ "$(jq length <<<"$records")" -eq 0 ]]; then
		echo "dns-clean: no A, AAAA, or ownership TXT records left for $domain"
		exit 0
	fi
	echo "dns-clean: deleting these records for $domain (ExternalDNS did not):"
	describe "$records" | sed 's/^/  /'
	batch=$(jq -c '{Changes: [.[] | {Action: "DELETE", ResourceRecordSet: .}]}' <<<"$records")
	aws route53 change-resource-record-sets --hosted-zone-id "$zone" --change-batch "$batch" \
		--output text --query 'ChangeInfo.Status' >/dev/null
	echo "dns-clean: deleted $(jq length <<<"$records") record(s)"
	;;
*)
	echo "usage: dns-clean.sh [clean|list]" >&2
	exit 2
	;;
esac
