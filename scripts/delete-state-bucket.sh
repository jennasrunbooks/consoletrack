#!/usr/bin/env bash
# Deletes the Terraform state bucket, ct-tfstate-<NAME_SUFFIX>, with every object version, for
# `make down-all` (after it destroyed the global root, whose state lives there). The bucket is
# versioned, so a plain `aws s3 rb --force` would leave it standing.
#
# Asks you to type the bucket name first, unless `make down-all` already did
# (DELETE_STATE_BUCKET_CONFIRMED=1). Reads AWS_REGION and NAME_SUFFIX from the environment.
set -euo pipefail

die() {
	echo "delete-state-bucket: $*" >&2
	exit 1
}

region=${AWS_REGION:?set AWS_REGION}
bucket="ct-tfstate-${NAME_SUFFIX:?set NAME_SUFFIX}"
aws() { command aws --region "$region" --no-cli-pager "$@"; }

confirmed=${DELETE_STATE_BUCKET_CONFIRMED:-}
[[ -n "$confirmed" || -t 0 ]] || die "run from an interactive terminal"
if ! aws s3api head-bucket --bucket "$bucket" 2>/dev/null; then
	echo "delete-state-bucket: $bucket doesn't exist; nothing to delete"
	exit 0
fi

# `make down-all` has already asked you to type the bucket name.
if [[ -z "$confirmed" ]]; then
	echo "This permanently deletes the Terraform state bucket $bucket and every version in it."
	read -r -p "Type the bucket name to confirm: " reply
	[[ "$reply" == "$bucket" ]] || die "aborted; nothing deleted"
fi

while :; do
	batch=$(aws s3api list-object-versions --bucket "$bucket" --max-items 500 \
		--query '{Objects: [Versions, DeleteMarkers][].{Key: Key, VersionId: VersionId}}' --output json)
	[[ "$(jq '(.Objects // []) | length' <<<"$batch")" -gt 0 ]] || break
	aws s3api delete-objects --bucket "$bucket" --delete "$(jq -c '{Objects: .Objects, Quiet: true}' <<<"$batch")" >/dev/null
done
aws s3api delete-bucket --bucket "$bucket"
echo "delete-state-bucket: deleted $bucket"
