#!/bin/sh
# Print the backend image tag: a content hash of exactly what the image is built
# from -- the Dockerfile and the files it COPYs (requirements.txt and app/).
#
# The hash is a git *tree* of those paths staged into a throwaway index (the real
# index and the shared stash stack are never touched), so the tag:
#   * is pure content, with no commit timestamp: identical inputs always hash the
#     same, so a rebuilt-but-unchanged image keeps its tag and the pod does not
#     roll;
#   * tracks only what the image actually contains -- so a different commit that
#     does not touch these inputs (a frontend or docs change, or an edit to
#     backend/tests/, which .dockerignore excludes) does NOT change the tag and
#     does not force a needless rollout; and
#   * includes untracked, non-ignored files under those paths, since docker would
#     COPY them into the image.
#
# A changed image always gets a new tag, so `kubectl apply` rolls the pod on its
# own. Prints nothing when not in a git repo (callers fall back).
set -u

CTX="${1:-backend}"

git rev-parse --git-dir >/dev/null 2>&1 || exit 0

# The paths the Dockerfile builds from, relative to the repo root. Only the ones
# that exist are hashed, so a non-standard layout degrades instead of failing.
INPUTS=""
for p in "$CTX/Dockerfile" "$CTX/requirements.txt" "$CTX/app"; do
	[ -e "$p" ] && INPUTS="$INPUTS $p"
done
[ -n "$INPUTS" ] || exit 0

# A unique name with the file removed, so `git add` writes a fresh empty index.
idx=$(mktemp "${TMPDIR:-/tmp}/piq-imgidx.XXXXXX" 2>/dev/null) && rm -f "$idx" || exit 0
trap 'rm -f "$idx"' EXIT

# shellcheck disable=SC2086 # INPUTS is a deliberate list of pathspecs
tree=$(GIT_INDEX_FILE="$idx" git add -A -- $INPUTS >/dev/null 2>&1 &&
	GIT_INDEX_FILE="$idx" git write-tree 2>/dev/null) || tree=
[ -n "$tree" ] || exit 0

printf '%s\n' "$(printf '%s' "$tree" | cut -c1-12)"
