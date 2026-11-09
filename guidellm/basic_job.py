#!/usr/bin/env python3
"""GuideLLM 기본 동작을 이해하기 위한 단일 파일 예제 (표준 라이브러리만 사용).

EvalHub REST API 로 아래 4단계를 그대로 보여준다.
  1) provider 준비   : GuideLLM 이미지를 쓰는 provider 를 테넌트에 등록 (이미 있으면 재사용)
  2) job 제출        : "어떤 모델에, 어떤 부하를, 얼마 동안" 줄지 담아 POST
  3) 완료 대기       : job 상태를 10초마다 조회
  4) 결과 출력       : GuideLLM 이 측정한 지표를 표로 출력

실행: python3 basic_job.py      (oc login 상태여야 함. 토큰은 `oc whoami -t` 로 가져온다)
"""
import json
import ssl
import subprocess
import time
import urllib.request

# ── 환경 (이 파일에서 직접 고친다) ─────────────────────────────────────────
EVALHUB_URL = "https://evalhub-redhat-ods-applications.apps.rhoai-35.edwin.home"
TENANT = "kbl-eval"                      # 평가 job 이 실행될 네임스페이스
MODEL_URL = "http://qwen3-4b-instruct-2507-v10-metrics.model-hub.svc.cluster.local:8080/v1"
MODEL_NAME = "qwen3-4b-instruct-2507-v10"
TOKENIZER = "Qwen/Qwen3-4B-Instruct-2507"  # 합성 프롬프트를 정확한 토큰 길이로 만들 때 사용
GUIDELLM_IMAGE = "quay.io/evalhub/community-guidellm:v0.5.0"
PROVIDER_NAME = "guidellm_basic"

# ── 부하 (GuideLLM 에 그대로 전달되는 값) ──────────────────────────────────
LOAD = {
    "profile": "concurrent",                         # 동시 사용자 수를 고정해서 부하를 준다
    "rate": 4,                                       # 동시 사용자 4명
    "max_seconds": 30,                               # 30초 동안 측정
    "data": "prompt_tokens=256,output_tokens=128",   # 요청마다 입력 256 / 출력 128 토큰
    "processor": TOKENIZER,
    "request_type": "chat_completions",              # /v1/chat/completions 로 호출
}

TOKEN = subprocess.run(["oc", "whoami", "-t"], capture_output=True, text=True, check=True).stdout.strip()
CTX = ssl._create_unverified_context()   # lab 라우터 인증서가 사설 CA 이므로 검증 생략


def api(method, path, body=None):
    """EvalHub REST 호출. 모든 요청에 토큰과 테넌트(X-Tenant) 헤더가 필요하다."""
    req = urllib.request.Request(
        EVALHUB_URL + path, method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": f"Bearer {TOKEN}", "X-Tenant": TENANT,
                 "Content-Type": "application/json"})
    with urllib.request.urlopen(req, context=CTX, timeout=60) as resp:
        return json.loads(resp.read() or b"{}")


# 1) provider 준비 ─────────────────────────────────────────────────────────
#    provider = "어떤 컨테이너 이미지로 평가를 실행할지" + "그 안에 어떤 벤치마크가 있는지" 정의
providers = api("GET", "/api/v1/evaluations/providers?limit=100").get("items", [])
provider = next((p for p in providers if p.get("name") == PROVIDER_NAME), None)
if provider is None:
    provider = api("POST", "/api/v1/evaluations/providers", {
        "name": PROVIDER_NAME,
        "title": "GuideLLM basic",
        "description": "GuideLLM 기본 동작 확인용",
        "runtime": {"k8s": {"image": GUIDELLM_IMAGE, "entrypoint": ["python", "main.py"],
                            "cpu_request": "100m", "memory_request": "256Mi",
                            "cpu_limit": "1000m", "memory_limit": "2Gi"}},
        "benchmarks": [{
            "id": "basic_load", "name": "기본 부하 테스트", "category": "performance",
            "metrics": ["requests_per_second", "output_tokens_per_second", "mean_ttft_ms", "mean_itl_ms"],
            "primary_score": {"metric": "mean_ttft_ms", "lower_is_better": True},
            "pass_criteria": {"threshold": 1000},   # 첫 토큰 평균 1초 이하면 PASS
        }],
    })
provider_id = provider["resource"]["id"]
print(f"[1] provider: {PROVIDER_NAME} ({provider_id})")

# 2) job 제출 ──────────────────────────────────────────────────────────────
job_request = {
    "name": "guidellm-basic",
    "model": {"url": MODEL_URL, "name": MODEL_NAME},
    "benchmarks": [{"provider_id": provider_id, "id": "basic_load", "parameters": LOAD}],
}
print("[2] job 요청:\n" + json.dumps(job_request, ensure_ascii=False, indent=2))
job_id = api("POST", "/api/v1/evaluations/jobs", job_request)["resource"]["id"]
print(f"    job id: {job_id}")

# 3) 완료 대기 ─────────────────────────────────────────────────────────────
#    EvalHub 가 kbl-eval 에 pod 를 만든다: init(데이터) → sidecar(프록시) + adapter(GuideLLM 실행)
start = time.time()
while True:
    job = api("GET", f"/api/v1/evaluations/jobs/{job_id}")
    state = job["status"]["state"]
    print(f"[3] {time.time() - start:5.0f}s  {state}")
    if state in ("completed", "failed", "cancelled", "partially_failed"):
        break
    time.sleep(10)

# 4) 결과 출력 ─────────────────────────────────────────────────────────────
if state != "completed":
    print("실패:", json.dumps(job["status"], ensure_ascii=False, indent=2))
    raise SystemExit(1)

bench = job["results"]["benchmarks"][0]
m, test = bench["metrics"], bench.get("test") or {}
print(f"""
[4] 결과  ({LOAD['profile']}, 동시 {LOAD['rate']}명, {LOAD['max_seconds']}초, {LOAD['data']})
  처리량       요청/초          {m.get('requests_per_second', 0):8.2f}   서버가 1초에 끝낸 요청 수
               출력 토큰/초     {m.get('output_tokens_per_second', 0):8.1f}   서버 전체가 1초에 생성한 토큰
  지연         TTFT 평균(ms)    {m.get('mean_ttft_ms', 0):8.0f}   질문 후 첫 토큰까지
               ITL 평균(ms)     {m.get('mean_itl_ms', 0):8.1f}   토큰 사이 간격 (1000/ITL = 사용자당 초당 토큰)
  판정         {test.get('primary_score_metric')} {test.get('primary_score')} (기준 {test.get('threshold')}) → {'PASS' if test.get('pass') else 'FAIL'}

  p95 등 상세 분포: oc logs -n {TENANT} $(oc get pods -n {TENANT} -o name | grep {job_id[:26]}) -c adapter
""")
