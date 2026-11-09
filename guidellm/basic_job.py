#!/usr/bin/env python3
"""GuideLLM 기본 동작을 이해하기 위한 단일 파일 예제 (EvalHub Python SDK 사용).

RHOAI 에 포함된 시스템 provider `guidellm` 을 그대로 써서 아래 3단계를 보여준다.
  1) job 제출   : "어떤 모델에, 어떤 부하를, 얼마 동안" 줄지 담아 제출
  2) 완료 대기  : SDK 가 상태를 주기적으로 조회
  3) 결과 출력  : GuideLLM 이 측정한 지표 + 로그의 p95 요약 표

전제: EvalHub CR(evaluation/evalhub/hub/evalhub.yaml)의 spec.providers 에 `guidellm` 이 켜져 있어야 한다.

실행 (설치 없이):
    uv run --with "eval-hub-sdk[client]==1.0.7" python basic_job.py
또는  pip install "eval-hub-sdk[client]==1.0.7" && python3 basic_job.py
(oc login 상태여야 함. 토큰은 `oc whoami -t` 로 가져온다.
 RHOAI 3.5 EvalHub 서버는 1.0.0 이다. SDK 는 같은 1.0.x 를 쓴다 — 1.0.0, 1.0.7 로 동작 확인)
"""
import json
import subprocess

from evalhub.client import SyncEvalHubClient
from evalhub.models import BenchmarkConfig, JobSubmissionRequest, ModelConfig

# ── 환경 (이 파일에서 직접 고친다) ─────────────────────────────────────────
EVALHUB_URL = "https://evalhub-redhat-ods-applications.apps.rhoai-35.edwin.home"
TENANT = "kbl-eval"                      # 평가 job 이 실행될 네임스페이스
MODEL_URL = "http://qwen3-4b-instruct-2507-v10-metrics.model-hub.svc.cluster.local:8080/v1"
MODEL_NAME = "qwen3-4b-instruct-2507-v10"
TOKENIZER = "Qwen/Qwen3-4B-Instruct-2507"  # 합성 프롬프트를 정확한 토큰 길이로 만들 때 사용
PROVIDER_ID = "guidellm"                 # RHOAI 시스템 provider (이미지: quay.io/evalhub/community-guidellm)
BENCHMARK_ID = "concurrent"              # 시스템 provider 의 "Fixed concurrency stress test"
TTFT_SLA_MS = 1000                       # 판정 기준: 첫 토큰 평균 1초 이하면 PASS

# ── 부하 (GuideLLM 에 그대로 전달되는 값. 실제 동작은 benchmark id 가 아니라 이 값이 정한다) ──
LOAD = {
    "profile": "concurrent",                         # 동시 사용자 수를 고정해서 부하를 준다
    "rate": 4,                                       # 동시 사용자 4명
    "max_seconds": 30,                               # 30초 동안 측정
    "data": "prompt_tokens=256,output_tokens=128",   # 요청마다 입력 256 / 출력 128 토큰
    "processor": TOKENIZER,
    "request_type": "chat_completions",              # /v1/chat/completions 로 호출
}

token = subprocess.run(["oc", "whoami", "-t"], capture_output=True, text=True, check=True).stdout.strip()
# 토큰·테넌트(X-Tenant)·재시도를 SDK 가 처리한다. lab 라우터 인증서가 사설 CA 라 검증은 끈다.
client = SyncEvalHubClient(base_url=EVALHUB_URL, auth_token=token, tenant=TENANT, insecure=True)

# 시스템 provider 가 켜져 있는지 확인 (EvalHub CR spec.providers 에 guidellm 이 있어야 함)
if not any(p.resource.id == PROVIDER_ID for p in client.providers.list()):
    raise SystemExit(f"provider '{PROVIDER_ID}' 가 없습니다. EvalHub CR 의 spec.providers 에 guidellm 을 추가하세요.")

# 1) job 제출 ──────────────────────────────────────────────────────────────
request = JobSubmissionRequest(
    name="guidellm-basic",
    model=ModelConfig(url=MODEL_URL, name=MODEL_NAME),
    benchmarks=[BenchmarkConfig(id=BENCHMARK_ID, provider_id=PROVIDER_ID, parameters=LOAD)],
)
print("[1] job 요청:\n" + json.dumps(request.model_dump(exclude_none=True), ensure_ascii=False, indent=2))
job = client.jobs.submit(request)
print(f"    job id: {job.resource.id}")

# 2) 완료 대기 ─────────────────────────────────────────────────────────────
#    EvalHub 가 kbl-eval 에 pod 를 만든다: init(데이터) → sidecar(프록시) + adapter(GuideLLM 실행)
print("[2] 완료 대기 중... (이미지가 처음이면 몇 분 더 걸린다)")
job = client.jobs.wait_for_completion(job.resource.id, timeout=900, poll_interval=10)
state = job.status.state.value
print(f"    상태: {state}")

# 3) 결과 출력 ─────────────────────────────────────────────────────────────
if state != "completed":
    print("실패:", job.status.message.message if job.status.message else job.status)
    raise SystemExit(1)

m = job.results.benchmarks[0].metrics
ttft = m.get("mean_ttft_ms", 0)
# 서버 측 판정은 시스템 벤치마크의 기본 기준(output_tokens_per_second ≥ 10)으로 하지만,
# SDK 응답 모델이 그 결과(test 필드)를 버리므로 여기서는 TTFT 기준으로 직접 판정한다.
print(f"""
[3] 결과  ({LOAD['profile']}, 동시 {LOAD['rate']}명, {LOAD['max_seconds']}초, {LOAD['data']})
  처리량       요청/초          {m.get('requests_per_second', 0):8.2f}   서버가 1초에 끝낸 요청 수
               출력 토큰/초     {m.get('output_tokens_per_second', 0):8.1f}   서버 전체가 1초에 생성한 토큰
  지연         TTFT 평균(ms)    {ttft:8.0f}   질문 후 첫 토큰까지
               ITL 평균(ms)     {m.get('mean_itl_ms', 0):8.1f}   토큰 사이 간격 (1000/ITL = 사용자당 초당 토큰)
  판정         TTFT 평균 {ttft:.0f}ms ≤ {TTFT_SLA_MS}ms → {'PASS' if ttft <= TTFT_SLA_MS else 'FAIL'}
""")

# 중앙값/p95 는 EvalHub 지표로 오지 않고 GuideLLM 로그의 요약 표에만 있다 → SDK 로 job 로그를 가져온다
logs = client.jobs.get_logs(job.resource.id, benchmark_index=0)
lines = logs.splitlines()
start = next((i for i, l in enumerate(lines) if "Request Latency Statistics" in l), None)
if start is not None:
    print("  GuideLLM 요약 (Request Latency: E2E 초, TTFT/ITL/TPOT ms, Mdn=중앙값)")
    for line in lines[start + 1:start + 8]:
        print("   ", line.split("GuideLLM: ")[-1])
else:
    print("  (로그에서 요약 표를 찾지 못함)")
