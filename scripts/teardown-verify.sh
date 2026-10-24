#!/usr/bin/env bash
# Looks for billable AWS resources tagged project=consoletrack (and ENIs in a project VPC), for `make verify-clean` and for
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

# Every ENI that is tagged, or that sits in a project VPC: the VPC CNI's leftovers (described
# "aws-K8S-i-<instance>", status available) carry no project tag, and they are what blocks deleting
# the subnets. Each line shows the status, so a leftover reads "(available)".
network_interfaces() {
	describe_enis "$tag" | eni_lines
	local vpcs
	vpcs=$(aws ec2 describe-vpcs --filters "$tag" --query 'Vpcs[].VpcId' --output text | tr '\t' ',')
	[[ -n "$vpcs" ]] || return 0
	describe_enis "Name=vpc-id,Values=$vpcs" | eni_lines
}

# Rows of "<eni>\t<status>\t<description>" for one EC2 filter.
describe_enis() {
	aws ec2 describe-network-interfaces --filters "$1" \
		--query 'NetworkInterfaces[].[NetworkInterfaceId,Status,Description]' --output text
}

eni_lines() {
	awk -F'\t' 'NF { printf "eni %s (%s) %s\n", $1, $2, $3 }'
}

# The ELB-owned ENIs only, for `wait`: AWS creates them untagged, described "ELB <name>".
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
		if grep -q '(available) aws-K8S-' <<<"$found"; then
			echo "teardown-verify: (available) aws-K8S-* are VPC CNI leftovers that block deleting the subnets;" \
				"remove them with: make clean-enis" >&2
		fi
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
