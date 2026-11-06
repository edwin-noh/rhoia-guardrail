#!/usr/bin/env bash
# EvalHub 평가 job 제출 → 완료까지 대기 → 결과 표 출력
#   ./submit.sh 01-kmmlu.json            # 예제① KMMLU
#   ./submit.sh 02-golden.json           # 예제② 골든셋
#   ./submit.sh 03-release-collection.json
# 환경변수: TENANT(기본 kbl-eval), TOKEN(기본: kbl-eval-runner SA 토큰), EVALHUB_URL(기본: route)
# 필요: oc, curl, jq
set -euo pipefail
DIR=$(cd "$(dirname "$0")" && pwd)
BODY=${1:?요청 파일 (예: 01-kmmlu.json)}; [[ -f $BODY ]] || BODY=$DIR/$BODY
TENANT=${TENANT:-kbl-eval}
EVALHUB_URL=${EVALHUB_URL:-https://$(oc get route evalhub -n redhat-ods-applications -o jsonpath='{.spec.host}')}
TOKEN=${TOKEN:-$(oc create token kbl-eval-runner -n "$TENANT" --duration=2h)}
api() { curl -sk -H "Authorization: Bearer $TOKEN" -H "X-Tenant: $TENANT" -H 'Content-Type: application/json' "$@"; }

job=$(api -X POST "$EVALHUB_URL/api/v1/evaluations/jobs" -d @"$BODY")
id=$(echo "$job" | jq -r '.resource.id // .id // empty')
[[ -n $id ]] || { echo "제출 실패: $job"; exit 1; }
echo "job: $id  ($(jq -r .name "$BODY"))"

while :; do
  res=$(api "$EVALHUB_URL/api/v1/evaluations/jobs/$id")
  state=$(echo "$res" | jq -r '.status.state')
  echo "  $(date +%H:%M:%S) $state $(echo "$res" | jq -r '[.status.benchmarks[]? | "\(.id):\(.status)"] | join(" ")')"
  case $state in completed|failed|cancelled|partially_failed) break;; esac
  sleep 20
done

echo
echo "$res" | jq -r '
  "== 결과 (\(.status.state)) ==",
  (.results.benchmarks[]? |
     "\(.id)\t" + ([.metrics | to_entries[] | "\(.key)=\(.value)"] | join("  ")) +
     (if .test then "\t[\(if .test.pass then "PASS" else "FAIL" end) \(.test.primary_score_metric // "")=\(.test.primary_score // "") ≥ \(.test.threshold // "")]" else "" end)),
  (if .results.test then "== 종합: \(if .results.test.pass then "PASS" else "FAIL" end) score=\(.results.test.score) threshold=\(.results.test.threshold) ==" else empty end),
  (if .results.mlflow_experiment_url then "MLflow: \(.results.mlflow_experiment_url)" else empty end),
  (if .status.message then "message: \(.status.message | tostring)" else empty end)'
echo "$res" > "$DIR/last-result.json"
