#!/bin/sh
# Print the image tag for a build: the short HEAD commit, plus a deterministic
# "-dirty-<hash>" suffix when the build context differs from HEAD.
#
# The suffix is a git *tree* hash of the context directory (CTX, default
# "backend"), computed in a throwaway index so the real index and the shared
# stash stack are never touched. A tree hash is pure content -- it carries no
# commit timestamp -- so, unlike `git stash create`, two calls a second apart
# on an unchanged tree agree, and a rebuilt-but-identical image keeps its tag.
# It also:
#   * includes untracked, non-ignored files that `docker build` copies in, so
#     a new un-`git add`ed source file still changes the tag; and
#   * is scoped to the context `docker build` actually sends, so edits
#     elsewhere in the repo (frontend, docs) don't churn the image tag.
#
# Prints just the short HEAD when the context matches HEAD, and prints nothing
# when not in a git repo (same graceful degradation as before).
set -u

CTX="${1:-backend}"

short_head=$(git rev-parse --short HEAD 2>/dev/null) || exit 0
[ -n "$short_head" ] || exit 0
printf '%s' "$short_head"

# A unique name with the file removed, so `git add` writes a fresh empty index.
idx=$(mktemp "${TMPDIR:-/tmp}/piq-imgidx.XXXXXX" 2>/dev/null) && rm -f "$idx" || {
	printf '\n'
	exit 0
}
trap 'rm -f "$idx"' EXIT

# `git write-tree` hashes the whole index, so pull out the CTX subtree and
# compare it to HEAD's CTX subtree -- same nesting on both sides.
work_full=$(GIT_INDEX_FILE="$idx" git add -A -- "$CTX" >/dev/null 2>&1 &&
	GIT_INDEX_FILE="$idx" git write-tree 2>/dev/null) || work_full=
work_ctx=
[ -n "$work_full" ] && work_ctx=$(git rev-parse -q --verify "$work_full:$CTX" 2>/dev/null)
head_ctx=$(git rev-parse -q --verify "HEAD:$CTX" 2>/dev/null) || head_ctx=

if [ -n "$work_ctx" ] && [ "$work_ctx" != "$head_ctx" ]; then
	printf -- '-dirty-%s' "$(printf '%s' "$work_ctx" | cut -c1-8)"
fi
printf '\n'
