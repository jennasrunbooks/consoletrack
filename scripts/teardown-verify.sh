#!/usr/bin/env bash
# Looks for billable AWS resources tagged project=consoletrack, for `make verify-clean` and for
# `make down`, which waits for the load balancers and their ENIs to go before it removes the
# Load Balancer Controller's cluster and VPC.
#
#   teardown-verify.sh verify   list leftover load balancers, ENIs, EBS volumes, Elastic IPs, and
#                               NAT gateways; exit 1 if any remain
#   teardown-verify.sh wait     poll until no load balancers or ELB-owned ENIs remain
#                               (WAIT_TIMEOUT seconds, default 900); exit 1 on timeout
#
# Read-only AWS calls. Reads AWS_REGION (and AWS_PROFILE) from the environment. Prints resource
# names and IDs only, never an ARN or account ID (any 12-digit number is masked).
set -euo pipefail

mode=${1:-verify}
region=${AWS_REGION:?set AWS_REGION}
tag=Name=tag:project,Values=consoletrack

mask() {
	sed -E 's/[0-9]{12}/***/g'
}

command -v aws >/dev/null || {
	echo "teardown-verify: needs the AWS CLI" >&2
	exit 2
}
aws() {
	command aws --region "$region" --no-cli-pager "$@"
}

# Each finder prints one "<kind> <id>" line per leftover.
load_balancers() {
	local arns
	arns=$(aws elbv2 describe-load-balancers --query 'LoadBalancers[].LoadBalancerArn' --output text)
	[[ -n "$arns" ]] || return 0
	# describe-tags takes at most 20 ARNs per call.
	local -a all
	read -ra all <<<"$arns"
	local i
	for ((i = 0; i < ${#all[@]}; i += 20)); do
		aws elbv2 describe-tags --resource-arns "${all[@]:i:20}" --query \
			"TagDescriptions[?Tags[?Key=='project' && Value=='consoletrack']].ResourceArn" --output text
	done | tr '\t' '\n' | sed -E 's|.*loadbalancer/||; /^$/d; s|^|load-balancer |'
}

# Tagged ENIs, plus the ones the Load Balancer Controller's ALBs own in a project VPC (AWS
# creates those untagged, with the description "ELB <name>").
network_interfaces() {
	aws ec2 describe-network-interfaces --filters "$tag" \
		--query 'NetworkInterfaces[].NetworkInterfaceId' --output text | tr '\t' '\n' | sed '/^$/d; s/^/eni /'
	elb_enis
}

elb_enis() {
	local vpcs
	vpcs=$(aws ec2 describe-vpcs --filters "$tag" --query 'Vpcs[].VpcId' --output text | tr '\t' ',')
	[[ -n "$vpcs" ]] || return 0
	aws ec2 describe-network-interfaces \
		--filters "Name=vpc-id,Values=$vpcs" 'Name=description,Values=ELB *' \
		--query 'NetworkInterfaces[].NetworkInterfaceId' --output text | tr '\t' '\n' | sed '/^$/d; s/^/eni /'
}

volumes() {
	aws ec2 describe-volumes --filters "$tag" --query 'Volumes[].VolumeId' --output text |
		tr '\t' '\n' | sed '/^$/d; s/^/ebs-volume /'
}

elastic_ips() {
	aws ec2 describe-addresses --filters "$tag" --query 'Addresses[].AllocationId' --output text |
		tr '\t' '\n' | sed '/^$/d; s/^/elastic-ip /'
}

nat_gateways() {
	aws ec2 describe-nat-gateways \
		--filter "$tag" 'Name=state,Values=pending,available,deleting' \
		--query 'NatGateways[].NatGatewayId' --output text | tr '\t' '\n' | sed '/^$/d; s/^/nat-gateway /'
}

case "$mode" in
verify)
	found=$({
		load_balancers
		network_interfaces
		volumes
		elastic_ips
		nat_gateways
	} | sort -u | mask)
	if [[ -n "$found" ]]; then
		echo "teardown-verify: project=consoletrack resources still exist in $region:" >&2
		# shellcheck disable=SC2001 # indents every line
		sed 's/^/  /' <<<"$found" >&2
		exit 1
	fi
	echo "teardown-verify: no project=consoletrack load balancers, ENIs, EBS volumes, Elastic IPs, or NAT gateways in $region"
	;;
wait)
	deadline=$((SECONDS + ${WAIT_TIMEOUT:-900}))
	while :; do
		found=$({
			load_balancers
			elb_enis
		} | sort -u | mask)
		if [[ -z "$found" ]]; then
			echo "teardown-verify: no project=consoletrack load balancers or ELB ENIs remain"
			exit 0
		fi
		if ((SECONDS >= deadline)); then
			echo "teardown-verify: still waiting after ${WAIT_TIMEOUT:-900}s for:" >&2
			# shellcheck disable=SC2001 # indents every line
		sed 's/^/  /' <<<"$found" >&2
			exit 1
		fi
		echo "teardown-verify: waiting for $(wc -l <<<"$found" | tr -d ' ') resource(s) to go..."
		sleep 15
	done
	;;
*)
	echo "usage: teardown-verify.sh verify|wait" >&2
	exit 2
	;;
esac
