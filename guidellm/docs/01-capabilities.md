# RHOAI 3.5 성능 테스트 기능 정리 (EvalHub · GuideLLM · 연동 기능)

RHOAI 3.5 가 성능(부하) 테스트용으로 **기본 제공하는 기능**과, GuideLLM 과 **연동해 쓸 수 있는 기능**(결과 확인, 모델 인증, 테스트 데이터, MaaS, 모니터링)을 정리한다.
고객 요구에 맞춘 구성은 [02-architecture.md](02-architecture.md) 를 본다.

## 근거 표기

| 표기 | 의미 |
|---|---|
| **[문서]** | RHOAI Self-Managed 3.5 공식 문서 PDF. 문서 이름과 절 번호를 함께 적는다 |
| **[클러스터]** | 테스트 클러스터(SNO, OCP 4.22 + RHOAI 3.5)에서 2026-11-14 에 조회한 값 |
| **[커뮤니티]** | Red Hat Developer 글이나 업스트림 자료. **3.5 미확인** |

문서와 클러스터가 다르면 숨기지 않고 둘 다 적는다. 지원 수준(GA / TP / DP)은 *Release notes* 기준이다.

---

## 1. 요약

| 항목 | 3.5 기본 제공 여부 | 지원 수준 | 비고 |
|---|---|---|---|
| EvalHub (평가 오케스트레이션, REST/SDK/CLI) | 제공 | **GA** | 성능 테스트도 EvalHub job 으로 실행한다 |
| GuideLLM provider (`guidellm`) | 제공 (built-in, 읽기 전용) | 별도 표기 없음 | 문서에는 이름만 있다. benchmark, 파라미터, 지표는 클러스터에서 확인해야 한다 |
| GuideLLM 이 들어간 기본 collection | **없음** | - | 필요하면 직접 만든다 |
| Dashboard Evaluations 화면 | 제공 | **TP** | 실행, 결과 조회, 비교(MLflow) |
| MLflow 결과 기록 | 제공 | (EvalHub GA 의 일부) | job 에 `experiment` 를 지정해야 기록된다 |
| 모델 API key / SA 토큰 인증 | 제공 | GA | MaaS 를 직접 언급하지는 않는다 |
| 모델 엔드포인트 사용자 CA 지정 | **문서에 없음** | - | 클러스터에서는 내부 service CA 엔드포인트(8443)에 연결하지 못한다 |
| 사용자 테스트 데이터 (S3, PVC) | 제공 | GA | GuideLLM 이 요구하는 데이터 형식은 문서에 없다 |
| 플랫폼 관측 대시보드 (Observe & monitor) | 제공 | **TP** | Models 탭: P90 TTFT, 처리량, GPU 사용률 |
| llm-d Perses 대시보드 | 제공 | **GA** | Traffic, Performance, Utilization |
| MaaS 사용량 대시보드 | 제공 | **TP** | 토큰, 요청 수, rate limit 위반 |
| GuideLLM 부하 테스트 절차, 사이징 방법론 | **문서에 없음** | - | 이 저장소의 [BEST-PRACTICES.md](../BEST-PRACTICES.md) 로 보완한다 |

---

## 2. EvalHub

**[문서]** *Evaluating AI systems* 2장, *Release notes* 2.1 "EvalHub general availability" (**GA**)

- **개념** (2.1)
  - **Provider**: 평가 프레임워크. 예: `lm_evaluation_harness`, `garak`, `guidellm`, `lighteval`
  - **Benchmark**: provider 안의 개별 평가 작업
  - **Collection**: 여러 provider 의 benchmark 를 묶은 재사용 가능한 평가 세트. 가중치와 통과 기준을 둔다
  - built-in provider 는 읽기 전용이다
- **REST API** (2.25)
  - job: `POST/GET /api/v1/evaluations/jobs`, `GET/DELETE /api/v1/evaluations/jobs/{id}`
  - provider, collection: CRUD
  - 헤더: `Authorization: Bearer <token>`, `X-Tenant: <namespace>`
- **결과 형식**
  - `results.benchmarks[].metrics` 에 지표가 들어간다
  - `test` 에 `pass_criteria` 기준 통과 여부가 들어간다
- **CLI / SDK** (2.4, 2.10~2.12)
  - 설치: `pip install "eval-hub-sdk[cli]"`
  - 명령: `evalhub providers list|describe`, `evalhub eval run --config …`, `evalhub eval status --watch`, `evalhub eval results <id> --format table|json|yaml|csv`
  - `evalhub eval logs` 는 TP 다
- **MCP** (4장): `discover_providers`, `submit_evaluation` 등. **TP**
- **Kueue 연동**: **TP**
- **Custom provider** (2.21, 2.22, 2.24)
  - 정의: 이름, adapter 컨테이너 이미지(UBI9), benchmark 목록
  - 등록: ConfigMap 이나 REST API
  - adapter 작성: Python SDK 의 `FrameworkAdapter`

**[클러스터]**
- EvalHub CR: `redhat-ods-applications/evalhub`
  - Argo CD App `kbl-evaluation-evalhub` 가 관리한다. 원본은 [evaluation/evalhub/hub/evalhub.yaml](../../evaluation/evalhub/hub/evalhub.yaml)
  - tenant: `kbl-eval`
  - Route: `https://evalhub-redhat-ods-applications.apps.rhoai-35.edwin.home`
  - 켜진 provider: `lm-evaluation-harness`, `guidellm`, `kbl-guidellm`, `kbl-lm-eval-ko`, `kbl-golden`
- **job 이 끝나면 Kubernetes Job 과 Pod 가 정리된다.** 결과는 EvalHub REST API 와 MLflow 에만 남는다. pod 로그를 읽는 방식(현재 `report.py` 의 p95 추출)은 정리된 뒤에는 동작하지 않는다.

---

## 3. GuideLLM provider

### 3.1 문서에 있는 내용

**[문서]** *Evaluating AI systems*

| 위치 | 내용 |
|---|---|
| 2.1 개념 | provider 예시로 `guidellm` |
| 2.3 EvalHub CR 예시 | `providers:` 에 `- guidellm` |
| 2.9 provider 조회 예시 | `guidellm  GuideLLM  Performance benchmarking  4` |
| 2.25.2 Table 2.8 Built-in providers | `guidellm  7  Guidance language model evaluation.` |

- **문서끼리 맞지 않는다.** benchmark 수는 4 와 7 로 다르고, Table 2.8 의 설명("Guidance language model evaluation")은 GuideLLM 의 실제 용도(성능 벤치마크)와 다르다.
- **benchmark id, profile, 파라미터, 지표는 문서에 없다.** 문서에 있는 것은 benchmark 별 `parameters` 필드 하나뿐이다(2.24).
- *Release notes* 에 GuideLLM 자체의 지원 수준 항목은 없다. "Evaluation Stack user interface"(TP) 와 EvalHub 기능 설명에 지원 프레임워크 중 하나로 이름만 나온다.

### 3.2 클러스터에서 확인한 built-in provider

**[클러스터]** `GET /api/v1/evaluations/providers`, ConfigMap `trustyai-service-operator-evalhub-provider-guidellm`

- 이미지: `quay.io/evalhub/community-guidellm:v0.5.4` (**community 이미지**. Red Hat 지원 범위를 확인해야 한다)
- 리소스: request 100m / 128Mi, limit 1000m / 2Gi

| benchmark id | 설명 (provider 정의) | primary score | 기본 통과 기준 |
|---|---|---|---|
| `sweep` | 저부하부터 고부하까지 훑어 처리량과 지연의 trade-off 탐색 | `output_tokens_per_second` | 10 |
| `throughput` | 포화될 때까지 부하를 올려 최대 처리량 탐색 | `output_tokens_per_second` | 10 |
| `concurrent` | 고정 동시성 부하 | `output_tokens_per_second` | 10 |
| `constant` | 고정 요청률 부하 | `output_tokens_per_second` | 10 |
| `poisson` | Poisson 도착 분포(실제 트래픽 모사) | `output_tokens_per_second` | 10 |
| `quick_perf_test` | 짧은 표본으로 빠른 성능 확인 | `requests_per_second` | 1 |
| `comprehensive_perf_test` | 모든 profile 로 종합 측정 | `output_tokens_per_second` | 10 |

- **보고하는 지표**: `requests_per_second`, `prompt_tokens_per_second`, `output_tokens_per_second`, `mean_ttft_ms`, `mean_itl_ms` (모두 평균값)
- **provider 에 기본 파라미터가 없다.** 실제 부하는 job 의 `parameters` 로 정한다. 이 저장소에서 동작을 확인한 파라미터는 다음과 같다.

  | 파라미터 | 예 | 의미 |
  |---|---|---|
  | `profile` | `synchronous`, `concurrent`, `constant`, `poisson`, `throughput`, `sweep` | 부하 방식 |
  | `rate` | `8` | 동시성 또는 요청률. `throughput` 에서는 필수(없으면 실패) |
  | `max_seconds` | `120` | 측정 시간 |
  | `max_requests` | `500` | 최대 요청 수 |
  | `data` | `prompt_tokens=256,output_tokens=256` | 합성 데이터 토큰 길이 |
  | `processor` | `Qwen/Qwen3-4B-Instruct-2507` | 토큰 수를 셀 tokenizer |
  | `request_type` | `chat_completions` | `/v1/chat/completions` 호출 |
  | `warmup` | `5` | 앞부분 제외. `"10%"` 는 10초로 처리된다 |
  | `outputs` | `json,csv,yaml` | 결과 파일 형식. `html` 은 템플릿을 인터넷에서 받으므로 폐쇄망에서는 제외한다 |

  benchmark id 는 정의를 고르는 이름일 뿐이고, 실제 부하는 `profile` 과 `rate` 가 정한다([README.md](../README.md) 참고).

### 3.3 기본 collection

**[문서]** *Evaluating AI systems* 2.13 Table 2.2 / **[클러스터]** 같음

| collection | 구성 |
|---|---|
| `leaderboard-v2` | lm_evaluation_harness (ifeval, bbh, gpqa, mmlu_pro, musr, math_hard) |
| `safety-and-fairness-v1` | lm_evaluation_harness (truthfulqa, toxigen, winogender, crows_pairs, bbq, ethics) |
| `toxicity-and-ethical-principles` | lm_evaluation_harness (toxigen, truthfulqa_mc1, hhh_alignment) |

- **GuideLLM 이 들어간 기본 collection 은 없다.**
- 클러스터의 operator catalog 에는 다른 collection 도 있다(coding-v1, long-context-v1, model-validation 등). 이 EvalHub CR 에서 켜지 않았고 문서 Table 2.2 에도 없다.
- **[커뮤니티]** Red Hat Developer "EvalHub: Capability and safety benchmarking for AI models"(2026-07-09)에는 GuideLLM `quick_perf_test` 와 `sweep` 를 다른 provider 와 함께 collection 에 넣고 `weight` 와 `pass_criteria` 로 판정하는 예제가 있다. 다만 이 저장소의 확인 결과로는 `sweep` 같은 다단계 profile 은 **첫 단계만 지표로 보고**되므로 판정용으로 쓰기 어렵다(adapter v0.7.2 에서 바뀌었는지는 미확인).

### 3.4 이 저장소의 커스텀 provider

| provider | 등록 방식 | 이미지 | MLflow 기록 | 용도 |
|---|---|---|---|---|
| `guidellm` | built-in (EvalHub CR `spec.providers`) | `community-guidellm:v0.5.4` | 지표 없음 | 비교용 |
| `kbl_guidellm` | ConfigMap [provider-kbl-guidellm.yaml](../../evaluation/evalhub/hub/provider-kbl-guidellm.yaml) (GitOps) | adapter v0.7.2 (digest 고정) | 있음 | [basic_job.py](../basic_job.py) |
| `kbl_perf` | REST API, tenant 범위 ([provider-kbl-perf.json](../provider-kbl-perf.json)) | adapter v0.7.2 | 있음 | [run.sh](../run.sh): KB Life 업무 시나리오 5종 |

- v0.7.2 는 job 에 `experiment` 가 있으면 benchmark 마다 MLflow run 을 만든다.
  - Metrics: rps, tok/s, 평균 TTFT, 평균 ITL
  - Artifacts: `benchmarks.json/csv/html` (p50/p95 같은 분포 포함)
- 한 job 안의 benchmark 는 **동시에** 실행되어 서로 간섭한다. 그래서 시나리오마다 job 을 하나씩 순차로 제출한다(`run.py`).

---

## 4. 결과 확인 기능

| 방법 | 근거 | 지원 수준 | 보이는 것 |
|---|---|---|---|
| **Dashboard → Develop & train → Evaluations** | [문서] *Evaluating AI systems* 3장 | **TP** (*Release notes* "Evaluation Stack user interface", 결과 비교 화면) | 실행 목록, benchmark 결과, 통과 여부, MLflow 기반 run 비교. 화면에서 직접 실행할 수도 있다(Endpoint URL, Benchmark parameters JSON) |
| **MLflow** | [문서] 2.20 | EvalHub GA 의 일부 | experiment 와 run, 지표, artifact(`benchmarks.json` 등), `evalcard.json` |
| **REST / CLI** | [문서] 2.25, 2.12 | GA | `results.benchmarks[].metrics`, `test`, `mlflow_run_id` |
| **이 저장소의 `report.py`** | 커스텀 | - | 시나리오별 SLA 표 (`sla-report.md`, `sla-report.csv`) |

- **Evaluations 화면은 기본으로 숨겨져 있다.** OdhDashboardConfig 에 `spec.dashboardConfig.disableLMEval: false` 를 넣어야 보인다([문서] *Managing resources* 2.3 "Dashboard configuration options" 기본값 `true`, *Evaluating AI systems* 6.5 NOTE).
  - *Evaluating AI systems* 3.1, 3.2 는 `disableEvalHub` 를 false 로 하라고 적었지만, rhods-operator 3.5.3 의 OdhDashboardConfig CRD 에는 이 필드가 없어 효과가 없다([클러스터]).
  - 이 클러스터에서는 `openshift-cluster-gitops` 의 `components-trustyai` 에 patch 를 넣어 켰다.
- Known issue **RHOAIENG-66068**: Dashboard 는 `redhat-ods-applications` 에 있는 EvalHub 만 지원한다. 그래서 이 저장소는 EvalHub CR 을 그 namespace 에 둔다. 문서 2.3 은 반대로 전용 namespace 를 권장하므로 둘이 충돌한다.
- **[클러스터]**
  - `eval-hub-ui` pod 가 실행 중이다.
  - MLflow v3.14.0 이 `redhat-ods-applications` 에 있다. 외부 주소 `https://rh-ai.apps.rhoai-35.edwin.home/mlflow`, EvalHub 에는 `MLFLOW_TRACKING_URI=https://mlflow.redhat-ods-applications.svc:8443/mlflow` 로 설정되어 있다.
- 문서에는 MLflow run 의 지표 이름 규칙이 없다(*Release notes* 는 "standardized schema" 라고만 한다).

---

## 5. 모델 접근 (인증 · TLS)

**[문서]** *Evaluating AI systems*

- **2.15 API key 인증**
  - `api-key` 키를 가진 Secret 을 만들고 job 에 `"model": {"url": …, "name": …, "auth": {"secret_ref": "<secret>"}}` 로 지정한다.
  - MaaS 의 `sk-oai-…` 키에 이 방식을 쓸 수 있다. 다만 **문서는 MaaS 를 언급하지 않는다.**
- **2.16 ServiceAccount 토큰**: kube-rbac-proxy 가 앞에 있는 모델(Raw 배포에서 `security.opendatahub.io/enable-auth: "true"`)에 SA 토큰을 주입한다.
- **모델 엔드포인트의 사용자 CA 나 TLS 검증 생략 설정은 문서에 없다.** TLS 설정은 EvalHub 서버와 MLflow 용만 있다.

**[클러스터]**
- job 의 sidecar proxy 는 시스템 CA 만 신뢰한다. 그래서 OpenShift service CA 로 서명된 `*-predictor.<ns>.svc:8443` 에 연결하지 못한다.
- 그래서 지금까지는 vLLM 8080 포트로 직접 호출했다(`model-hub` 모델, 인증 없음). 실제 서비스 경로보다 낙관적인 측정이다.
- **해법 (2026-11-15 확인, 3.5 문서 미기재)**: sidecar 는 `secret_ref` Secret 에 **`ca_cert`** 키가 있으면 그 CA 를 시스템 CA 에 더해 TLS 를 검증한다. `api-key` 가 없으면 job SA(`evalhub-<EvalHub ns>-job`) 토큰을 넣는다.
  - 근거: eval-hub 소스(`internal/eval_runtime_sidecar/proxy/http_client.go` 의 "Model CA cert absent in secret mount", `model_proxy.go`)와 클러스터 `eval-runtime-sidecar` 바이너리의 같은 문자열
  - GuideLLM adapter 는 `api-key` 가 있을 때만 `--backend-kwargs` 로 ref 토큰을 넘기고, CA 와 SA 토큰은 sidecar 에 맡긴다(eval-hub-contrib `adapters/guidellm/main.py`)

---

## 6. 사용자 테스트 데이터

**[문서]** *Evaluating AI systems*

- **2.17 S3**: `test_data_ref.s3 {bucket, key, secret_ref}`. init container 가 받아 `/test_data` 에 둔다.
- **2.18 PVC**: `test_data_ref.pvc {claim_name, sub_path}` 를 `/test_data` 에 읽기 전용으로 마운트한다(RWX/ROX 권장).
- collection 안에서도 쓸 수 있다. local mode 에서는 쓸 수 없다.
- "The expected file format and directory structure of the test data depend on the adapter and benchmark." **GuideLLM 의 데이터 형식은 문서에 없다.**
- OCI 데이터셋 소스는 문서에 없다.

**[클러스터]**
- S3 호환 저장소 rustfs 와 Secret `kbl-eval-s3` 가 있다(golden set 평가에서 사용).
- 2026-11-14 현재 **GuideLLM job 에서 `/test_data` 의 프롬프트 파일을 `data` 로 지정하는 방식은 아직 확인하지 않았다.**

**[커뮤니티]** GuideLLM 의 `data` 는 합성 설정 문자열 외에 파일(jsonl, csv 등)과 Hugging Face 데이터셋도 받는다(업스트림 `vllm-project/guidellm`). Red Hat Developer "Benchmarking with GuideLLM in air-gapped OpenShift clusters"(2025-09) 글은 폐쇄망에서의 실행을 다룬다.

---

## 7. Models-as-a-Service (MaaS)

**[문서]** *Govern LLM access with Models-as-a-Service*

- **호출** (2.2.5)
  - 헤더: `Authorization: Bearer ${MAAS_API_KEY}` (`sk-oai-` 접두어)
  - 권장 방식은 body 라우팅이다: `${MAAS_URL}/v1/chat/completions` 에 body 의 `model` 로 모델을 고른다
  - 경로 라우팅 `${MAAS_URL}/llm/<model>/v1/…` 은 legacy 다. 두 방식 모두 같은 구독, 토큰 제한, rate limit 이 적용된다
  - Dashboard 에서 만든 임시 키는 1시간 뒤 만료된다
- **제한** (1장 MaaSSubscription, 2.2.7)
  - `tokenRateLimits: - limit: <N> window: 1m`: 모델별, 창(window)별 토큰 상한
  - 넘으면 `429` `token_limit_error` 와 `X-RateLimit-*`, `Retry-After` 헤더를 돌려준다
  - **요청 수(RPS) 제한은 문서에 없다.** 토큰 기반 제한만 있다
- **부하 테스트 시 주의**
  - llm-d 오토스케일링 예제(*Deploy models using distributed inference with llm-d* 13.5)는 "rate limit 없이 충분한 부하를 주기 위해" 인증을 끄는 annotation 을 쓴다. 문서는 "Production deployments must not set this annotation" 이라고 경고한다.
  - 이 저장소는 인증을 끄는 대신 **성능 테스트 전용 구독**을 쓴다([02-architecture.md](02-architecture.md)).
- **관측** (1.18)
  - Perses 기반 사용량 대시보드(cluster admin): 토큰 사용량, 요청 수, rate limit 위반, CSV export. **TP**
  - 지표: `authorized_hits_total`, `authorized_calls_total`, `limited_calls_total`, `istio_request_duration_milliseconds_bucket`
  - 사용자 레이블은 기본 꺼짐(`captureUser: false`)

**[클러스터]**
- LLMInferenceService `kbl-maas/qwen3-1-7b` (Ready): `https://maas.apps.rhoai-35.edwin.home/kbl-maas/qwen3-1-7b`
- MaaSSubscription `kbl-ai-developers`: 사용자당 `20000` tokens / `1m` (PoC 값, `rhoai-serving-gitops/ops/maas-policy/subscription-kbl-ai.yaml`). 부하 테스트를 하면 몇 초 안에 429 가 난다.

---

## 8. 모니터링 연동

### 8.1 RHOAI / OpenShift 기본 기능

| 기능 | 근거 | 지원 수준 | 성능 테스트에서 볼 것 |
|---|---|---|---|
| User workload monitoring (UWM) | [문서] *Managing and monitoring models* 2.6 | OCP 기능 | vLLM 지표 수집의 전제 |
| Observe & monitor → Dashboard (Cluster, Models, Usage, LLM Traffic, LLM Performance, LLM Utilization 탭) | [문서] *Managing OpenShift AI* 12.2 | **TP** (*Release notes* "Centralized platform observability") | Models 탭: P90 E2E, P90 TTFT, 토큰 처리량, 큐 길이, replica 수, 성공률, GPU 사용률 |
| 모델별 성능 지표 화면 | [문서] *Deploying models* 2.12 | - | 요청 수, 평균 응답 시간, CPU/메모리 |
| llm-d Perses 참조 대시보드 | [문서] llm-d 17장, *Release notes* 3.5 GA 신규 기능 | **GA** | EPP, 스케줄러, vLLM 지표 |
| Grafana | [문서] *Managing and monitoring models* 2.7, llm-d 17.7 | 예시 overlay (제품 아님) | vLLM 과 DCGM 대시보드 import |
| MaaS 사용량 대시보드 | [문서] MaaS 1.18 | **TP** | 토큰, 요청 수, 429 횟수 |

- **주요 vLLM 지표**: `vllm:time_to_first_token_seconds`, `vllm:inter_token_latency_seconds`(또는 `time_per_output_token`), `vllm:e2e_request_latency_seconds`, `vllm:num_requests_running`, `vllm:num_requests_waiting`, `vllm:kv_cache_usage_perc`(`gpu_cache_usage_perc`), 토큰 카운터
- **GPU 지표**: DCGM exporter 의 `DCGM_FI_DEV_GPU_UTIL`, `DCGM_FI_DEV_FB_USED` 등. 문서는 DCGM 대시보드 절차를 외부 문서로 넘긴다.

### 8.2 클러스터 구성과 `observability/` 자산

**[클러스터]**
- UWM 켜짐. `vllm:*` 지표 96종과 `DCGM_FI_DEV_GPU_UTIL` 이 Thanos Querier 로 조회된다.
- 모니터 리소스는 자동 생성된다.
  - Raw: ServiceMonitor `<isvc>-metrics`
  - llm-d: PodMonitor `kserve-llm-isvc-vllm-engine`, ServiceMonitor `kserve-llm-isvc-scheduler`
- 대시보드
  - COO(Cluster Observability Operator) v1.5.5 와 Perses: RHOAI 기본 대시보드와 llm-d 대시보드가 있다
  - Grafana operator v5.25: instance `grafana/grafana`, `https://grafana-route-grafana.apps.rhoai-35.edwin.home`, 대시보드 `vllm`, `dcgm`
- `../../../observability/` 자산

  | 경로 | 내용 |
  |---|---|
  | `01-model-serving-monitoring/` | PersesDashboard 와 GrafanaDashboard `kbl-model-serving` (Raw, llm-d 공통 vLLM 지표) |
  | `02-maas-usage-monitoring/` | `kbl-maas-usage` (Limitador 지표) |
  | `common/` | Thanos Querier 와 RHOAI Prometheus 데이터소스 |
- `rhoai-serving-gitops` 의 PrometheusRule 알림: TTFT P90 > 2s, 오류율 > 5%, waiting > 5, KV cache > 90%

---

## 9. 사이징 관련 근거

- **[문서]** GuideLLM 부하 테스트 절차나 사이징 방법론은 **없다.** 관련 내용은 다음뿐이다.
  - *Managing and monitoring models* 2.5.1: "N parameters in 16-bit precision requires approximately 2N bytes" 와 모델 크기별 GPU 등급
  - TTFT, ITL, TPS, RPS 정의 (2.5, 2.5.3)
  - 오토스케일링 절: "Run load tests with realistic sequence length distributions."
  - *Working with the model catalog* "Performance insights": 검증된 모델의 TTFT/E2E/ITL 백분위와 권장 replica 수. 측정에 쓴 도구 이름은 없다.
- **이 저장소**: [BEST-PRACTICES.md](../BEST-PRACTICES.md) §7 사이징 공식(요청률 × E2E ÷ GPU당 동시성 × 여유율), §6 서버 지표 PromQL

---

## 10. 참고 (커뮤니티, 3.5 미확인)

- Red Hat Developer, [EvalHub: Capability and safety benchmarking for AI models](https://developers.redhat.com/articles/2026/07/09/evalhub-capability-and-safety-benchmarking-ai-models) (2026-07-09): GuideLLM collection 예제(`warmup`, `cooldown`, `pass_criteria`)
- Red Hat Developer, [Benchmarking with GuideLLM in air-gapped OpenShift clusters](https://developers.redhat.com/articles/2025/09/15/benchmarking-guidellm-air-gapped-openshift-clusters) (2025-09)
- Red Hat Developer, [How to deploy and benchmark vLLM with GuideLLM on Kubernetes](https://developers.redhat.com/articles/2025/12/24/how-deploy-and-benchmark-vllm-guidellm-kubernetes) (2025-12)
- Red Hat Developer, [GuideLLM: Evaluate LLM deployments for real-world inference](https://developers.redhat.com/articles/2025/06/20/guidellm-evaluate-llm-deployments-real-world-inference) (2025-06)
- 업스트림: `github.com/vllm-project/guidellm`
