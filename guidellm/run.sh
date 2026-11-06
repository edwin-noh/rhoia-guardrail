#!/usr/bin/env bash
# KB Life 서빙 성능 평가 실행
#   ./run.sh                                   # config.env 기본값 (시나리오당 30초, 5개)
#   MAX_SECONDS=60 ./run.sh                    # 측정 시간 변경 (모든 시나리오에 우선 적용)
#   SCENARIOS=chat_baseline,chat_peak ./run.sh # 일부 시나리오만
#   RATE_SCALE=0.5 ./run.sh                    # 동시 사용자 수 절반
# 필요: oc(로그인), curl, jq, uv
set -euo pipefail
DIR=$(cd "$(dirname "$0")" && pwd)
# 사용자가 직접 준 MAX_SECONDS 는 시나리오별 값보다 우선한다
export MAX_SECONDS_OVERRIDE=${MAX_SECONDS:-}
set -a; source "$DIR/config.env"; set +a
exec uv run --quiet --no-project --with pyyaml python -I -u "$DIR/run.py"
