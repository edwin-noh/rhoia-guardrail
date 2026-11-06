#!/usr/bin/env bash
# EvalHub 에 필요한 Secret 3개를 SealedSecret 으로 만들어 evalhub/secrets/ 에 쓴다 (Git 에 커밋 가능).
# SealedSecret 은 해당 클러스터의 sealed-secrets 키로만 복호화되므로, 다른 클러스터에서는 이 스크립트를 다시 실행한다.
#
#   S3 접속정보: 기본은 rustfs (rustfs/rustfs-credentials). 다른 S3 는 AWS_* 환경변수로 지정.
#   DB 비밀번호: 지정하지 않으면 무작위 생성 (PG_PASSWORD)
set -euo pipefail
DIR=$(cd "$(dirname "$0")/.." && pwd)
OUT=$DIR/evalhub/secrets
mkdir -p "$OUT"
SEAL="kubeseal --controller-namespace sealed-secrets --controller-name sealed-secrets-controller --format yaml"

AWS_ACCESS_KEY_ID=${AWS_ACCESS_KEY_ID:-$(oc get secret rustfs-credentials -n rustfs -o jsonpath='{.data.AWS_ACCESS_KEY_ID}' | base64 -d)}
AWS_SECRET_ACCESS_KEY=${AWS_SECRET_ACCESS_KEY:-$(oc get secret rustfs-credentials -n rustfs -o jsonpath='{.data.AWS_SECRET_ACCESS_KEY}' | base64 -d)}
S3_ENDPOINT_INTERNAL=${S3_ENDPOINT_INTERNAL:-http://rustfs.rustfs.svc.cluster.local:9000}
PG_PASSWORD=${PG_PASSWORD:-$(openssl rand -hex 16)}

# 1) 평가 job 이 S3 테스트 데이터를 내려받을 때 쓰는 자격증명 (test_data_ref.s3.secret_ref)
oc create secret generic kbl-eval-s3 -n kbl-eval --dry-run=client -o yaml \
  --from-literal=AWS_ACCESS_KEY_ID="$AWS_ACCESS_KEY_ID" \
  --from-literal=AWS_SECRET_ACCESS_KEY="$AWS_SECRET_ACCESS_KEY" \
  --from-literal=AWS_DEFAULT_REGION=us-east-1 \
  --from-literal=AWS_S3_ENDPOINT="$S3_ENDPOINT_INTERNAL" | $SEAL > "$OUT/kbl-eval-s3.yaml"

# 2) PostgreSQL 컨테이너 초기화 값
oc create secret generic evalhub-postgres -n kbl-eval --dry-run=client -o yaml \
  --from-literal=POSTGRESQL_USER=evalhub \
  --from-literal=POSTGRESQL_PASSWORD="$PG_PASSWORD" \
  --from-literal=POSTGRESQL_DATABASE=evalhub | $SEAL > "$OUT/evalhub-postgres.yaml"

# 3) EvalHub 서버가 읽는 DB 접속 문자열 (spec.database.secret, 키 db-url)
oc create secret generic evalhub-db-credentials -n redhat-ods-applications --dry-run=client -o yaml \
  --from-literal=db-url="postgres://evalhub:${PG_PASSWORD}@evalhub-postgres.kbl-eval.svc.cluster.local:5432/evalhub?sslmode=disable" \
  | $SEAL > "$OUT/evalhub-db-credentials.yaml"

ls -1 "$OUT"
