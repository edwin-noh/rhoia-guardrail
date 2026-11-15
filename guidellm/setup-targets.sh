#!/usr/bin/env bash
# targets.yaml 의 대상에 접속할 때 쓸 Secret 을 테넌트(kbl-eval)에 만든다(있으면 갱신).
# EvalHub job 은 model.auth.secret_ref 로 이 Secret 을 받고, sidecar 가 키를 다음처럼 쓴다.
#   ca_cert : 모델 엔드포인트 TLS 를 검증할 CA (없으면 시스템 CA 만 신뢰해 사설 CA 엔드포인트에서 x509 오류)
#   api-key : 있으면 Authorization: Bearer 로 보낸다. 없으면 sidecar 가 job SA 토큰을 넣는다
#
#   ./setup-targets.sh raw                       # kbl-perf-raw : ca_cert = OpenShift service CA
#                                                #   job SA(evalhub-redhat-ods-applications-job) 토큰으로 kube-rbac-proxy 인증
#   MAAS_API_KEY=sk-oai-... ./setup-targets.sh maas
#                                                # kbl-perf-maas : api-key + ca_cert = 인그레스(라우터) CA
#                                                #   API key 는 성능 테스트 전용 그룹(kbl-perf-testers) 사용자가 발급한다
# CA 는 공개 정보다. API key 는 화면이나 파일에 남기지 않는다(환경변수로만 받는다).
set -euo pipefail
DIR=$(cd "$(dirname "$0")" && pwd)
source "$DIR/config.env"

apply_secret() {  # $1=이름, 나머지=--from-file/--from-literal 인자
  local name=$1; shift
  oc create secret generic "$name" -n "$TENANT" "$@" --dry-run=client -o yaml \
    | oc label --local -f - app.kubernetes.io/part-of=kbl-guidellm -o yaml \
    | oc apply -f - >/dev/null
  echo "secret/$name ($TENANT): $(oc get secret "$name" -n "$TENANT" -o go-template='{{range $k,$v := .data}}{{$k}} {{end}}')"
}

tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT
for target in "${@:-raw}"; do
  case $target in
    raw)
      # 모든 네임스페이스에 주입되는 service CA ConfigMap
      oc get configmap openshift-service-ca.crt -n "$TENANT" -o jsonpath='{.data.service-ca\.crt}' > "$tmp/ca_cert"
      apply_secret kbl-perf-raw --from-file=ca_cert="$tmp/ca_cert"
      ;;
    maas)
      [[ -n ${MAAS_API_KEY:-} ]] || { echo "MAAS_API_KEY 환경변수가 필요합니다 (sk-oai-...)" >&2; exit 1; }
      # 인그레스 기본 인증서의 CA (maas-default-gateway 가 router-certs-default 를 쓴다)
      oc get configmap default-ingress-cert -n openshift-config-managed -o jsonpath='{.data.ca-bundle\.crt}' > "$tmp/ca_cert"
      printf '%s' "$MAAS_API_KEY" > "$tmp/api-key"
      apply_secret kbl-perf-maas --from-file=ca_cert="$tmp/ca_cert" --from-file=api-key="$tmp/api-key"
      ;;
    *) echo "알 수 없는 대상: $target (raw | maas)" >&2; exit 2 ;;
  esac
done
