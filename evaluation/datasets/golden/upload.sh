#!/usr/bin/env bash
# 골든셋 CSV → JSONL 변환(검증 포함) → S3 golden/<version>/ 업로드
#   ./upload.sh kbl-insurance-qa-v1.csv v1
# 필요: python3, uv, S3 접속 환경변수 (../common/s3-env.sh 를 source)
set -euo pipefail
DIR=$(cd "$(dirname "$0")" && pwd)
CSV=${1:?CSV 파일}; VERSION=${2:?버전 (예: v1)}
OUT=$DIR/build/$VERSION
rm -rf "$OUT"
python3 -I "$DIR/csv-to-jsonl.py" "$CSV" -o "$OUT/$(basename "${CSV%.csv}").jsonl"
uv run --quiet --no-project --with boto3 python -I "$DIR/../common/s3_sync.py" \
  "$OUT" "s3://${S3_BUCKET:-kbl-eval}/golden/$VERSION"
