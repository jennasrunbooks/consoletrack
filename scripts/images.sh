#!/usr/bin/env bash
# Builds every ConsoleTrack image for linux/arm64 (the Graviton nodes) and pushes it to GHCR as
# ghcr.io/jennasrunbooks/consoletrack-<name>:<IMAGE_TAG>. `make images` runs it, once before the
# first `make up`; deploy.yaml does the same in CI with GITHUB_TOKEN.
#
#   backend, frontend, k8s-mcp, demo-app   and aws-mcp, once mcp-server/aws has a Dockerfile
#
# Logs in with your GitHub CLI session and never shows the token:
#   gh auth token | docker login ghcr.io -u <user> --password-stdin
# The token needs the write:packages scope (`gh auth refresh -s write:packages`). Skip the login
# with IMAGES_SKIP_LOGIN=1 if you already logged in. Each GHCR package must be public once, so
# the nodes pull without an image pull secret (README → Deploy to AWS → Images).
#
# Reads IMAGE_TAG (default: the first 7 characters of the current commit) and GHCR_OWNER
# (default: jennasrunbooks, the owner the charts pull from). Needs Docker with buildx; amd64
# machines build arm64 through QEMU, which is slow but works.
set -euo pipefail

die() {
	echo "images: $*" >&2
	exit 1
}

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
owner=${GHCR_OWNER:-jennasrunbooks}
tag=${IMAGE_TAG:-$(git -C "$root" rev-parse HEAD | cut -c1-7)}
docker=${DOCKER:-docker}

command -v "$docker" >/dev/null || die "needs Docker with buildx (the devcontainer has none; run this on your machine)"
"$docker" buildx version >/dev/null 2>&1 || die "needs docker buildx"
if [[ -n "$(git -C "$root" status --porcelain)" ]]; then
	echo "images: warning: uncommitted changes; the images are tagged $tag but built from your working tree" >&2
fi

if [[ "${IMAGES_SKIP_LOGIN:-}" != 1 ]]; then
	command -v gh >/dev/null || die "needs the GitHub CLI (gh auth login), or IMAGES_SKIP_LOGIN=1 after docker login ghcr.io"
	user=$(gh api user --jq .login)
	if [[ "$user" == *-agent ]]; then
		die "gh is authenticated as $user; unset GH_TOKEN and run this as yourself"
	fi
	gh auth token | "$docker" login ghcr.io -u "$user" --password-stdin >/dev/null
	echo "images: logged in to ghcr.io as $user"
fi

# <image name>:<build context>
images=(backend:backend frontend:frontend k8s-mcp:mcp-server/k8s demo-app:demo-app)
if [[ -f "$root/mcp-server/aws/Dockerfile" ]]; then
	images+=(aws-mcp:mcp-server/aws)
else
	echo "images: aws-mcp has no Dockerfile yet, skipped"
fi

for entry in "${images[@]}"; do
	name=${entry%%:*}
	context=${entry#*:}
	ref="ghcr.io/$owner/consoletrack-$name:$tag"
	echo "images: building and pushing $ref (linux/arm64)"
	# The source label links the package to the repository, so its Actions access can be granted.
	"$docker" buildx build --platform linux/arm64 --tag "$ref" \
		--label "org.opencontainers.image.source=https://github.com/$owner/consoletrack" \
		--push "$root/$context"
done
echo "images: pushed ${#images[@]} images tagged $tag; deploy them with: make deploy IMAGE_TAG=$tag"
