#!/usr/bin/env bash
# 배포 승인용 컬렉션을 테넌트(kbl-eval)에 등록하고 id 를 출력한다. 같은 이름이 있으면 그 id 를 재사용한다.
#   ./create-collection.sh [collection-kbl-release-v1.json]
set -euo pipefail
DIR=$(cd "$(dirname "$0")" && pwd)
BODY=${1:-$DIR/collection-kbl-release-v1.json}
TENANT=${TENANT:-kbl-eval}
EVALHUB_URL=${EVALHUB_URL:-https://$(oc get route evalhub -n redhat-ods-applications -o jsonpath='{.spec.host}')}
TOKEN=${TOKEN:-$(oc create token kbl-eval-runner -n "$TENANT" --duration=1h)}
api() { curl -sk -H "Authorization: Bearer $TOKEN" -H "X-Tenant: $TENANT" -H 'Content-Type: application/json' "$@"; }

NAME=$(jq -r .name "$BODY")
id=$(api "$EVALHUB_URL/api/v1/evaluations/collections?limit=100" \
      | jq -r --arg n "$NAME" '(.items // .collections // [])[] | select(.name==$n) | (.resource.id // .id)' | head -1)
if [[ -z $id ]]; then
  res=$(api -X POST "$EVALHUB_URL/api/v1/evaluations/collections" -d @"$BODY")
  id=$(echo "$res" | jq -r '.resource.id // .id // empty')
  [[ -n $id ]] || { echo "등록 실패: $res" >&2; exit 1; }
fi
echo "$id"
