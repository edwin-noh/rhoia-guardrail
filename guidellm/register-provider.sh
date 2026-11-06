#!/usr/bin/env bash
# 성능 평가 provider(kbl_perf)를 EvalHub 테넌트에 REST API 로 등록(또는 갱신)하고 id 를 출력한다.
# ConfigMap(시스템 provider)이 아니라 API 로 등록하므로 EvalHub CR 을 수정할 필요가 없다.
#   ./register-provider.sh            # 없으면 생성, 있으면 provider-kbl-perf.json 내용으로 갱신(PUT)
set -euo pipefail
DIR=$(cd "$(dirname "$0")" && pwd)
source "$DIR/config.env"
EVALHUB_URL=${EVALHUB_URL:-https://$(oc get route evalhub -n redhat-ods-applications -o jsonpath='{.spec.host}')}
TOKEN=${TOKEN:-$(oc create token kbl-eval-runner -n "$TENANT" --duration=1h)}
api() { curl -sk -H "Authorization: Bearer $TOKEN" -H "X-Tenant: $TENANT" -H 'Content-Type: application/json' "$@"; }

id=$(api "$EVALHUB_URL/api/v1/evaluations/providers?limit=100" \
      | jq -r --arg n "$PROVIDER_NAME" '(.items // .providers // [])[] | select((.name // .resource.name)==$n) | (.resource.id // .id)' | head -1)
if [[ -z $id ]]; then
  res=$(api -X POST "$EVALHUB_URL/api/v1/evaluations/providers" -d @"$DIR/provider-kbl-perf.json")
else
  res=$(api -X PUT "$EVALHUB_URL/api/v1/evaluations/providers/$id" -d @"$DIR/provider-kbl-perf.json")
fi
id=$(echo "$res" | jq -r '.resource.id // .id // empty')
[[ -n $id ]] || { echo "provider 등록 실패: $res" >&2; exit 1; }
echo "$id"
