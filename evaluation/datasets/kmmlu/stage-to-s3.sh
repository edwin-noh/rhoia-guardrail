#!/usr/bin/env bash
# KMMLU 일부 과목을 받아 S3(rustfs) 에 EvalHub 오프라인 레이아웃으로 올린다.
#
#   ./stage-to-s3.sh                       # 인터넷 되는 PC 에서 다운로드 + 업로드
#   STAGE_ONLY=1 ./stage-to-s3.sh          # 폐쇄망: 다운로드만 → 반입 → UPLOAD_ONLY=1 로 업로드
#   UPLOAD_ONLY=1 STAGE_DIR=... ./stage-to-s3.sh
#
# 필요: uv, (업로드 시) S3 접속 환경변수 - ../common/s3-env.sh 를 source 하면 rustfs 로 설정된다
set -euo pipefail
DIR=$(cd "$(dirname "$0")" && pwd)
SUBJECTS=${SUBJECTS:-Accounting,Economics,Taxation,Law}   # 보험 업무 연관 과목
TOKENIZER=${TOKENIZER:-Qwen/Qwen3-4B-Instruct-2507}       # 평가 대상 모델의 tokenizer
STAGE_DIR=${STAGE_DIR:-$DIR/build/kmmlu-testdata}
S3_PREFIX=${S3_PREFIX:-datasets/kmmlu-kbl}

if [[ -z ${UPLOAD_ONLY:-} ]]; then
  rm -rf "$STAGE_DIR" && mkdir -p "$STAGE_DIR"
  # adapter 이미지(odh-ta-lmes-job)와 같은 datasets 버전으로 cache 를 만든다
  uv run --quiet --no-project --with 'datasets==5.0.1' --with 'transformers<5' python -I \
    "$DIR/stage.py" "$STAGE_DIR" "$SUBJECTS" "$TOKENIZER"
  rm -rf "$STAGE_DIR/xet"
  du -sh "$STAGE_DIR"
fi
[[ -n ${STAGE_ONLY:-} ]] && { echo "다운로드 완료: $STAGE_DIR (반입 후 UPLOAD_ONLY=1 로 업로드)"; exit 0; }

uv run --quiet --no-project --with boto3 python -I "$DIR/../common/s3_sync.py" \
  "$STAGE_DIR" "s3://${S3_BUCKET:-kbl-eval}/$S3_PREFIX"
