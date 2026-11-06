#!/usr/bin/env bash
# NeMo Guardrails 개인정보 가드 시나리오 테스트
#   - /v1/guardrail/checks : 응답 생성 없이 판정만 (config 3종 x 케이스)
#   - /v1/chat/completions : 실제 응답 (combined, 일부 케이스)
# 사용법: ./test-guardrails.sh [-n model-hub] [-r kbl-nemo-guardrails]
# 필요: oc(로그인 상태), curl, jq
set -euo pipefail

NS=model-hub
NAME=kbl-nemo-guardrails
while getopts "n:r:" o; do case $o in n) NS=$OPTARG;; r) NAME=$OPTARG;; *) exit 1;; esac; done

DIR=$(cd "$(dirname "$0")" && pwd)
CASES=$DIR/cases.json
OUT=$DIR/results/$(date +%Y%m%d-%H%M%S)
mkdir -p "$OUT"

HOST=$(oc get route "$NAME" -n "$NS" -o jsonpath='{.status.ingress[0].host}')
URL=https://$HOST
TOKEN=$(oc whoami -t)
CONFIGS=(pii-regex self-check combined)

call() {  # $1=path $2=json body
  curl -sk -X POST "$URL$1" -H "Content-Type: application/json" \
       -H "Authorization: Bearer $TOKEN" --max-time 120 -d "$2"
}

echo "Guardrails: $URL"
echo "결과 저장: $OUT"
echo
printf '%-3s %-44s' "#" "케이스"
for c in "${CONFIGS[@]}"; do printf ' %-18s' "$c"; done; echo
printf '%.0s-' {1..110}; echo

pass=0; fail=0
n=$(jq length "$CASES")
for ((i=0; i<n; i++)); do
  id=$(jq -r ".[$i].id" "$CASES"); desc=$(jq -r ".[$i].desc" "$CASES")
  msgs=$(jq -c ".[$i].messages" "$CASES")
  printf '%-3s %-44s' "$id" "$desc"
  for c in "${CONFIGS[@]}"; do
    body=$(jq -nc --argjson m "$msgs" --arg c "$c" '{model:"guardrails-test", messages:$m, guardrails:{config_id:$c}}')
    res=$(call /v1/guardrail/checks "$body" || echo '{"status":"error"}')
    echo "$res" > "$OUT/checks-$id-$c.json"
    st=$(echo "$res" | jq -r '.status // "error"' 2>/dev/null || echo error)
    exp=$(jq -r ".[$i].expect[\"$c\"]" "$CASES")
    if [[ $exp == any || $exp == "$st" ]]; then mark=OK; pass=$((pass+1)); else mark=XX; fail=$((fail+1)); fi
    printf ' %-18s' "$st($mark)"
  done
  echo
done
echo
echo "판정 일치: $pass / 불일치: $fail  (any = 결과 무관)"
echo

echo "=== /v1/chat/completions (combined, system 메시지 포함, LLM 호출 로그) ==="
SYS="너는 KB라이프 상담원이다. 반드시 한 문장으로 답해."
for id in 9 3 6; do
  msgs=$(jq -c --arg s "$SYS" '[{role:"system",content:$s}] + (.[] | select(.id=='"$id"') | .messages)' "$CASES")
  body=$(jq -nc --argjson m "$msgs" '{model:"qwen3-4b-instruct-2507-v10", max_tokens:200, messages:$m,
          guardrails:{config_id:"combined", options:{log:{activated_rails:true, llm_calls:true}}}}')
  res=$(call /v1/chat/completions "$body" || echo '{}')
  echo "$res" > "$OUT/chat-$id.json"
  echo "[$id] Q: $(echo "$msgs" | jq -r '.[-1].content')"
  echo "    A: $(echo "$res" | jq -r '.choices[0].message.content // .' | head -c 300)"
  echo "    rails: $(echo "$res" | jq -r '[.guardrails.log.activated_rails[]? | .name] | join(" → ")')"
  echo "    LLM 호출: $(echo "$res" | jq -r '[.guardrails.log.llm_calls[]? | .task] | join(", ")')"
  # 생성 호출(self_check_* 이 아닌 것)에 실제로 전달된 프롬프트 = passthrough 확인용
  echo "$res" | jq -r '.guardrails.log.llm_calls[]? | select(.task | startswith("self_check") | not) | "    → 모델에 전달된 프롬프트:\n" + (.prompt | tostring | .[0:400] | gsub("^"; "      "; "gm"))'
done
