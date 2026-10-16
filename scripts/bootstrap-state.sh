#!/usr/bin/env bash
# Create the Terraform state bucket, ct-tfstate-<NAME_SUFFIX>, with the AWS CLI. Run it once per
# account through `make bootstrap`, before the first `make tf-init`. Safe to re-run: an existing
# bucket is left exactly as it is.
#
# Reads AWS_PROFILE, AWS_REGION, and NAME_SUFFIX from the environment (the Makefile exports
# them from .env). If NAME_SUFFIX is empty it generates one and prints it; it never writes .env.
# Never prints the account ID: the caller ARN is shown with it masked, and every AWS CLI call
# goes through aws(), which masks the CLI's own error output (AccessDenied messages include
# the caller's ARN).
#
# Set BOOTSTRAP_ASSUME_YES=true to skip the confirmation prompt.
set -euo pipefail

die() {
	echo "bootstrap-state: $*" >&2
	exit 1
}

mask_account() {
	sed -E 's/[0-9]{12}/***/g'
}

# The AWS CLI with its stderr masked. stdout is passed through unchanged for callers that
# capture it; a 12-digit value there is only ever captured, never printed.
aws_errfile=$(mktemp)
trap 'rm -f "$aws_errfile"' EXIT
aws() {
	local rc=0
	command aws "$@" 2>"$aws_errfile" || rc=$?
	mask_account <"$aws_errfile" >&2
	return "$rc"
}

for v in AWS_PROFILE AWS_REGION; do
	[[ -n "${!v:-}" ]] || die "$v is not set (set it in .env or your shell)."
done
type -P aws >/dev/null || die "the AWS CLI v2 is required."

caller_arn=$(aws sts get-caller-identity --query Arn --output text) ||
	die "could not get the caller identity; is AWS_PROFILE=$AWS_PROFILE logged in?"
account_id=$(aws sts get-caller-identity --query Account --output text)
partition=$(cut -d: -f2 <<<"$caller_arn")

generated_suffix=false
suffix=${NAME_SUFFIX:-}
if [[ -z "$suffix" ]]; then
	# 6 lowercase letters or digits. head bounds the read, so tr never gets SIGPIPE.
	while [[ ${#suffix} -lt 6 ]]; do
		suffix+=$(head -c 64 /dev/urandom | LC_ALL=C tr -dc 'a-z0-9')
	done
	suffix=${suffix:0:6}
	generated_suffix=true
elif [[ ! "$suffix" =~ ^[a-z0-9]{4,8}$ ]]; then
	die "NAME_SUFFIX must be 4 to 8 lowercase letters or digits."
fi

bucket="ct-tfstate-${suffix}"

echo "AWS identity: $(mask_account <<<"$caller_arn")"
echo "Region:       $AWS_REGION"
echo "Bucket:       $bucket"
if [[ "${BOOTSTRAP_ASSUME_YES:-}" != "true" ]]; then
	read -r -p "Create the state bucket if it doesn't exist? [y/N] " reply
	[[ "$reply" =~ ^[Yy]$ ]] || die "aborted; nothing changed."
fi

print_suffix_hint() {
	if [[ "$generated_suffix" == "true" ]]; then
		echo
		echo "Generated NAME_SUFFIX=$suffix. Add this line to .env before any other make target:"
		echo
		echo "  NAME_SUFFIX=$suffix"
	fi
}

# head-bucket with the expected owner succeeds only for a bucket this account owns. A 403 means
# the name exists in someone else's account, so pick another suffix.
if head_err=$(aws s3api head-bucket --bucket "$bucket" --expected-bucket-owner "$account_id" 2>&1); then
	echo "Bucket $bucket already exists in this account; nothing changed."
	print_suffix_hint
	exit 0
fi
case "$head_err" in
*"Not Found"* | *404*) ;;
*"Forbidden"* | *403*) die "bucket $bucket exists but isn't this account's; set a different NAME_SUFFIX." ;;
*) die "could not check bucket $bucket: $(mask_account <<<"$head_err")" ;;
esac

echo "Creating $bucket..."
create_args=(--bucket "$bucket" --object-ownership BucketOwnerEnforced)
# us-east-1 is the default location and rejects an explicit LocationConstraint.
if [[ "$AWS_REGION" != "us-east-1" ]]; then
	create_args+=(--create-bucket-configuration "LocationConstraint=$AWS_REGION")
fi
aws s3api create-bucket "${create_args[@]}" >/dev/null
aws s3api wait bucket-exists --bucket "$bucket" --expected-bucket-owner "$account_id"

aws s3api put-public-access-block --bucket "$bucket" --public-access-block-configuration \
	BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true

# Versioning lets a bad state write be rolled back.
aws s3api put-bucket-versioning --bucket "$bucket" --versioning-configuration Status=Enabled

aws s3api put-bucket-encryption --bucket "$bucket" --server-side-encryption-configuration \
	'{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"AES256"}}]}'

aws s3api put-bucket-policy --bucket "$bucket" --policy "$(
	cat <<EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "DenyInsecureTransport",
      "Effect": "Deny",
      "Principal": "*",
      "Action": "s3:*",
      "Resource": ["arn:${partition}:s3:::${bucket}", "arn:${partition}:s3:::${bucket}/*"],
      "Condition": {"Bool": {"aws:SecureTransport": "false"}}
    }
  ]
}
EOF
)"

# owner is the suffix, like every other ConsoleTrack resource (#3), never the caller.
aws s3api put-bucket-tagging --bucket "$bucket" --tagging \
	"TagSet=[{Key=project,Value=consoletrack},{Key=owner,Value=${suffix}},{Key=managed_by,Value=bootstrap-state.sh}]"

echo "Created $bucket: versioned, SSE-S3, public access blocked, BucketOwnerEnforced, TLS only."
print_suffix_hint
