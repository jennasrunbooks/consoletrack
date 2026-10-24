#!/usr/bin/env bash
# The last step of `make up` / `make deploy` with EXPOSURE=alb: waits until https://DOMAIN really
# works, and says which step is stuck if it doesn't. Each pass checks, in order:
#
#   1. the Gateway is Programmed and has an address (the ALB's DNS name)
#   2. the zone has an A record for DOMAIN whose alias target is that ALB; an A record that points
#      elsewhere with no ExternalDNS ownership TXT is a stale one from a previous cluster, which
#      ExternalDNS will never change: fails at once, pointing at `make dns-clean`
#   3. every target group of the ALB has a healthy target
#   4. the zone's first name server answers DOMAIN with addresses (a health-evaluated alias answers
#      NOERROR with nothing while the targets are unhealthy, and resolvers cache that)
#   5. curl to the ALB's IP with DOMAIN's name gets a 2xx or 3xx
#
# Polls every WAIT_URL_INTERVAL seconds (15) for up to WAIT_URL_TIMEOUT (600); exits 1 naming the
# step that failed. Read-only. Reads DOMAIN, NAME_SUFFIX, DNS_ZONE_ID, AWS_REGION, and KUBE_CONTEXT
# from the environment (the Makefile exports them). Output masks any 12-digit number (account IDs).
set -euo pipefail

mask() {
	sed -E 's/[0-9]{12}/***/g'
}

# Everything printed goes through the mask.
exec > >(mask) 2> >(mask >&2)

die() {
	echo "wait-for-url: $*" >&2
	exit 2
}

for tool in aws kubectl jq dig curl; do
	command -v "$tool" >/dev/null || die "needs $tool"
done

domain=${DOMAIN:-}
[[ -n "$domain" ]] || die "DOMAIN is empty (EXPOSURE=alb needs it)"
domain=$(tr '[:upper:]' '[:lower:]' <<<"${domain%.}")
owner=${NAME_SUFFIX:?set NAME_SUFFIX}
zone=${DNS_ZONE_ID:-}
[[ "$zone" =~ ^Z[A-Z0-9]+$ ]] || die "no Route 53 zone ID (the global root's zone_id output)"
context=${KUBE_CONTEXT:-consoletrack-eks}
namespace=${GATEWAY_NAMESPACE:-consoletrack}
interval=${WAIT_URL_INTERVAL:-15}
timeout=${WAIT_URL_TIMEOUT:-600}
region=${AWS_REGION:?set AWS_REGION}

aws() {
	command aws --region "$region" --no-cli-pager "$@"
}

# The ALB name as Route 53 and the Gateway spell it differ in case, a trailing dot, and a
# "dualstack." prefix.
norm() {
	local n
	n=$(tr '[:upper:]' '[:lower:]' <<<"$1")
	n=${n%.}
	echo "${n#dualstack.}"
}

# Each check prints why it failed on stdout and returns 1, or sets what the later ones use.
alb=""
alb_ip=""
alb_state=""
failed_step=""
detail=""

fail() {
	failed_step=$1
	detail=$2
	return 1
}

check_gateway() {
	local gw
	gw=$(kubectl --context "$context" -n "$namespace" get gateways.gateway.networking.k8s.io -o json 2>&1) ||
		fail "1. Gateway" "kubectl could not list Gateways: $gw" || return 1
	alb=$(jq -r '[.items[] | select(any(.status.conditions[]?; .type == "Programmed" and .status == "True"))
		| .status.addresses[0].value // empty][0] // empty' <<<"$gw")
	[[ -n "$alb" ]] || fail "1. Gateway" "no Gateway in $namespace is Programmed with an address yet" || return 1
	alb=$(norm "$alb")
}

check_record() {
	local sets a_target txt
	sets=$(aws route53 list-resource-record-sets --hosted-zone-id "$zone" --output json) ||
		fail "2. DNS record" "could not list the zone's records" || return 1
	a_target=$(jq -r --arg d "$domain." '[.ResourceRecordSets[] | select(.Name == $d and .Type == "A")
		| .AliasTarget.DNSName // empty][0] // empty' <<<"$sets")
	if [[ -z "$a_target" ]]; then
		fail "2. DNS record" "no A record for $domain yet (ExternalDNS creates it about a minute after the Gateway is Programmed)" || return 1
	fi
	if [[ "$(norm "$a_target")" != "$alb" ]]; then
		txt=$(jq -r --arg d "$domain." --arg owner "$owner" '[.ResourceRecordSets[]
			| select(.Name == $d and .Type == "TXT" and any(.ResourceRecords[]?.Value;
				contains("heritage=external-dns,external-dns/owner=" + $owner)))] | length' <<<"$sets")
		if [[ "$txt" -eq 0 ]]; then
			echo "wait-for-url: step 2 failed: stale record from a previous cluster: the A record for $domain points at $(norm "$a_target")," >&2
			echo "wait-for-url: not this cluster's ALB ($alb), and has no ExternalDNS ownership TXT, so ExternalDNS won't change it." >&2
			echo "wait-for-url: delete it with: make dns-clean   (ExternalDNS then creates the right one within a minute)" >&2
			exit 1
		fi
		fail "2. DNS record" "the A record for $domain still points at $(norm "$a_target"), not $alb; ExternalDNS owns it and updates it on its next pass" || return 1
	fi
}

check_targets() {
	local lbs arn tgs tg health unhealthy="" any=0
	lbs=$(aws elbv2 describe-load-balancers --output json) ||
		fail "3. ALB targets" "could not describe load balancers" || return 1
	arn=$(jq -r --arg a "$alb" '[.LoadBalancers[] | select((.DNSName | ascii_downcase) == $a) | .LoadBalancerArn][0] // empty' <<<"$lbs")
	[[ -n "$arn" ]] || fail "3. ALB targets" "no load balancer named $alb in $region" || return 1
	alb_state=$(jq -r --arg a "$alb" '[.LoadBalancers[] | select((.DNSName | ascii_downcase) == $a) | .State.Code][0] // "unknown"' <<<"$lbs")
	alb_ip=$(dig +short A "$alb" | grep -E '^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$' | head -n1 || true)
	tgs=$(aws elbv2 describe-target-groups --load-balancer-arn "$arn" --output json |
		jq -r '.TargetGroups[].TargetGroupArn')
	[[ -n "$tgs" ]] || fail "3. ALB targets" "the ALB has no target groups yet" || return 1
	while read -r tg; do
		any=1
		health=$(aws elbv2 describe-target-health --target-group-arn "$tg" --output json)
		if ! jq -e 'any(.TargetHealthDescriptions[]?; .TargetHealth.State == "healthy")' <<<"$health" >/dev/null; then
			unhealthy+="  target group ${tg##*:}:"$'\n'
			unhealthy+=$(jq -r '.TargetHealthDescriptions[]? | "    \(.Target.Id) \(.TargetHealth.State)\(if .TargetHealth.Reason then " (" + .TargetHealth.Reason + ")" else "" end)"' <<<"$health")$'\n'
			[[ "$(jq '.TargetHealthDescriptions | length' <<<"$health")" -gt 0 ]] ||
				unhealthy+="    (no targets registered)"$'\n'
		fi
	done <<<"$tgs"
	((any)) || return 0
	[[ -z "$unhealthy" ]] || fail "3. ALB targets" "a target group has no healthy target:"$'\n'"${unhealthy%$'\n'}" || return 1
}

check_authoritative() {
	local ns answers full status eval alb_resolves="does not resolve"
	[[ -z "$alb_ip" ]] || alb_resolves=resolves
	ns=$(aws route53 get-hosted-zone --id "$zone" --output json | jq -r '.DelegationSet.NameServers[0] // empty')
	[[ -n "$ns" ]] || fail "4. Authoritative DNS" "the zone has no name servers" || return 1
	answers=$(dig +short +time=3 +tries=1 "@$ns" "$domain" A | grep -E '^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$' || true)
	[[ -n "$answers" ]] && return 0
	# Say why: the response status (none at all means the query timed out or was refused on the way),
	# and whether the alias is health-evaluated.
	full=$(dig +time=3 +tries=1 "@$ns" "$domain" A 2>&1 || true)
	status=$(grep -oE 'status: [A-Z]+' <<<"$full" | head -n1 || true)
	eval=$(aws route53 list-resource-record-sets --hosted-zone-id "$zone" --output json |
		jq -r --arg d "$domain." '[.ResourceRecordSets[] | select(.Name == $d and .Type == "A")
			| (.AliasTarget.EvaluateTargetHealth | tostring)][0] // "no A record"')
	fail "4. Authoritative DNS" "$ns answers $domain with no addresses (${status:-no response from dig: ${full:-no output}}; the A alias has EvaluateTargetHealth=$eval; the ALB is $alb_state and its own name $alb_resolves)" ||
		return 1
}

check_curl() {
	local code
	[[ -n "$alb_ip" ]] || fail "5. HTTPS" "could not resolve the ALB's address to test it" || return 1
	code=$(curl -sk --max-time 10 --resolve "$domain:443:$alb_ip" -o /dev/null -w '%{http_code}' "https://$domain/" || true)
	[[ "$code" =~ ^[23][0-9][0-9]$ ]] ||
		fail "5. HTTPS" "https://$domain/ through the ALB returned ${code:-no response} (is this machine's address in ALLOWED_CIDRS?)" ||
		return 1
}

deadline=$((SECONDS + timeout))
last=""
while :; do
	if check_gateway && check_record && check_targets && check_authoritative && check_curl; then
		break
	fi
	if ((SECONDS >= deadline)); then
		echo "wait-for-url: gave up after ${timeout}s; step $failed_step is not passing:" >&2
		# shellcheck disable=SC2001 # indents every line
		sed 's/^/  /' <<<"$detail" >&2
		exit 1
	fi
	if [[ "$failed_step" != "$last" ]]; then
		echo "wait-for-url: waiting at step $failed_step: ${detail%%$'\n'*}"
		last=$failed_step
	fi
	sleep "$interval"
done

echo "wait-for-url: https://$domain is up (Gateway Programmed, DNS record, healthy targets, authoritative answer, HTTPS 2xx/3xx)"
cat <<NOTE
wait-for-url: a resolver that answered "no such name" earlier may cache that for up to 15 minutes. If
$domain still doesn't load on this machine, flush the local cache:
  macOS:    sudo dscacheutil -flushcache; sudo killall -HUP mDNSResponder
  Chrome:   open chrome://net-internals/#dns and press "Clear host cache"
NOTE
