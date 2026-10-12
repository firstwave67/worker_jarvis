#!/usr/bin/env bash
# develop -> release: develop 트리에서 로컬 전용 경로를 뺀 스냅샷을 release 에 커밋 1개로 쌓는다.
# develop 이력은 release 로 넘어가지 않는다. 그 뒤 main 은 release 를 fast-forward 로 따라간다.
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"
msg=${1:?usage: ./release.sh "<release message>"}
LOCAL_ONLY=(prior-work.jsonl brain .release-deny)

export GIT_INDEX_FILE="$(git rev-parse --git-dir)/release-index"
git read-tree develop
git rm -rq --cached --ignore-unmatch "${LOCAL_ONLY[@]}"
tree=$(git write-tree)
rm -f "$GIT_INDEX_FILE"; unset GIT_INDEX_FILE

# 공개하면 안 되는 낱말(.release-deny, 줄마다 정규식)이 남아 있으면 멈춘다.
if [ -s .release-deny ] && git grep -nIE -f .release-deny "$tree"; then
  echo "release 중단: 위 줄을 develop 에서 정리하세요." >&2; exit 1
fi

parent=$(git rev-parse -q --verify refs/heads/release || true)
if [ -n "$parent" ] && [ "$(git rev-parse "$parent^{tree}")" = "$tree" ]; then
  echo "release 와 차이 없음"; exit 0
fi
commit=$(git commit-tree "$tree" ${parent:+-p "$parent"} -m "$msg")
git update-ref refs/heads/release "$commit"
echo "release -> $(git log --oneline -1 "$commit")"
