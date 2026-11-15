# 고객 요구 대응 아키텍처: Raw / MaaS 모델 부하 테스트와 사이징 검증

RHOAI 3.5 기본 기능([01-capabilities.md](01-capabilities.md))을 바탕으로 KB Life 요구 사항을 만족하는 구성을 정하고, 이를 위해 **구현해야 할 항목**을 정리한다.
2026-11-14 에 설계했고, 2026-11-15 에 코드와 매니페스트를 구현했다. 클러스터 적용과 실측 검증 상태는 아래 "구현 현황"을 본다.

## 구현 현황 (2026-11-15)

| # | 항목 | 상태 | 위치 |
|---|---|---|---|
| 1 | 대상 정의와 실행기 | 코드 완료 | `targets.yaml`, `run.py`(대상 × 시나리오, `max_model_len` 초과 시나리오 건너뜀), `config.env`(`TARGETS`) |
| 2 | Raw 경로 (8443, SA 토큰) | 매니페스트 작성, **클러스터 미적용** | `setup-targets.sh raw`(Secret `kbl-perf-raw`), rhoai-serving-gitops `ops/agent/kbl-eval.yaml`(RoleBinding), `evaluation/evalhub/tenant/namespace.yaml`(라벨) |
| 3 | MaaS 경로 (전용 구독) | 매니페스트 작성, **클러스터 미적용** | rhoai-serving-gitops `ops/platform/groups.yaml`(`kbl-perf-testers`), `ops/maas-policy/{authpolicy,subscription}-kbl-perf.yaml`, `setup-targets.sh maas`(Secret `kbl-perf-maas`) |
| 4 | 결과 리포트 | 완료 (예전 결과로 확인) | `report.py`: MLflow `benchmarks.json` 에서 p50/p95·요청 수 읽기, p95 판정, 대상 비교 표 |
| 5 | 모니터링 연계 | 완료 (링크 생성까지) | `report.py` + `targets.yaml` `dashboards`: 측정 구간 Grafana 링크. `kbl-model-serving`/`kbl-maas-usage` GrafanaDashboard 는 아직 클러스터에 없다(`observability/` 에서 적용 필요) |
| 6 | 고객 프롬프트 | 코드 완료, **실측 미확인** | `scenarios.yaml` `customer_prompts`(주석), `prompts-example.jsonl`, `provider-kbl-perf.json` 벤치마크 추가, `run.py` 가 `data`·`test_data_ref` 전달 |
| 7 | 기존 문서 정정 | 완료 | `README.md`, `BEST-PRACTICES.md`, `basic_job.py` |

**TLS 문제의 해법 (설계 때 PoC 로 남겨 둔 부분)**: EvalHub sidecar 는 `model.auth.secret_ref` Secret 에 `ca_cert` 키가 있으면 그 CA 로 모델 엔드포인트 TLS 를 검증한다.
`api-key` 키가 없으면 job SA 토큰을 Bearer 로 넣는다. 그래서 Raw 는 `ca_cert`(service CA)만, MaaS 는 `api-key` + `ca_cert`(인그레스 CA)를 넣는다.
근거는 eval-hub 소스(`internal/eval_runtime_sidecar/proxy/http_client.go`, `model_proxy.go`)와, 클러스터의 `eval-runtime-sidecar` 바이너리에 같은 메시지 문자열이 있다는 점이다. 3.5 문서에는 `ca_cert` 가 나오지 않는다(*Evaluating AI systems* 2.15, 2.16 은 `api-key` 와 SA 토큰만 설명).

**남은 확인 (클러스터 변경 필요)**: 정지된 모델(`serving.kserve.io/stop`) 기동, GitOps 반영, Secret 생성, 짧은 job 으로 Raw·MaaS 200 응답 확인, MaaS body 의 model 이름(`qwen3-1-7b`), `kbl-maas-usage` 의 subscription 레이블 값.

## 1. 요구 사항

> Raw(KServe RawDeployment)와 MaaS(llm-d)로 배포한 모델에 부하를 주어 성능을 측정하고, 계획한 리소스 사이징이 유효한지 점검한다.

| # | 요구 | 필수 여부 |
|---|---|---|
| R1 | GuideLLM 벤치마크로 모델에 부하/성능 테스트를 한다 | 필수 |
| R2 | 테스트 결과를 UI 나 리포트로 본다 | 필수 |
| R3 | 테스트하는 동안 리소스를 모니터링한다 (RHOAI/OpenShift 모니터링, Grafana 추가 시각화. `observability/` 에서 진행 중) | 필수 |
| R4 | 고객 데이터(프롬프트)로 성능 테스트를 한다 | 선택 |

## 2. 현재 상태와 차이

현재 [eval/guidellm](../) PoC 의 상태와 요구 대비 부족한 점이다.

| 항목 | 현재 PoC | 요구 대비 부족한 점 |
|---|---|---|
| 대상 모델 | `model-hub/qwen3-4b-instruct-2507-v10` (Dashboard 로 만든 모델, GitOps 밖, 지금은 정지) | GitOps 로 관리하는 Raw/MaaS 모델이 아니다 |
| 호출 경로 | vLLM 8080 직접 호출, 인증 없음 | 실제 서비스 경로(kube-rbac-proxy, MaaS Gateway, Authorino, Limitador)를 거치지 않아 낙관적이다 |
| 결과 | MLflow + `report.py` (md/csv). p95 는 pod 로그에서 추출 | pod 가 정리되면 p95 가 사라진다. 판정은 평균값 기준이다. Raw 와 MaaS 비교가 없다 |
| 모니터링 | 따로 본다 | 테스트 시간대와 대시보드가 연결되어 있지 않다 |
| 고객 데이터 | 합성 데이터(`prompt_tokens=N,output_tokens=M`)만 | 파일 데이터 경로를 확인하지 않았다 |

## 3. 목표 아키텍처

```
                       ┌──────────────────────────── kbl-eval (EvalHub tenant) ────────────────────────────┐
 운영자 / CI            │                                                                                   │
 run.sh ──(REST)──▶ EvalHub (redhat-ods-applications) ──▶ GuideLLM Job (provider kbl_perf, adapter v0.7.2)   │
 targets × scenarios   │                                  │  data: 합성 또는 /test_data/<prompts>.jsonl (R4)  │
                       └──────────────────────────────────┼───────────────────────────────────────────────┘
                                                          │
             ┌────────────────────────────────────────────┴───────────────────────────────┐
             ▼ (Raw)                                                                        ▼ (MaaS)
  https://qwen3-1-7b-predictor.kbl-models.svc:8443/v1                  https://maas.apps.rhoai-35.edwin.home/v1
  kube-rbac-proxy (SA 토큰, Role model-caller)                         maas-default-gateway → Authorino(API key)
        │                                                              → Limitador(성능 테스트 전용 구독) → EPP
        ▼                                                                       ▼
  InferenceService kbl-models/qwen3-1-7b (vLLM)                  LLMInferenceService kbl-maas/qwen3-1-7b (vLLM)

 결과 (R2)                                              모니터링 (R3)
  ├─ MLflow: experiment kbl-perf-<target>                 UWM / Thanos Querier
  ├─ Dashboard → Develop & train → Evaluations (TP)        ├─ Perses: kbl-model-serving, llm-d, kbl-maas-usage, RHOAI Models 탭
  └─ report.py: SLA 표(p95), Raw vs MaaS 비교,             └─ Grafana: vllm, dcgm, kbl-model-serving
     대시보드 링크(job 시작~종료 시간 범위)  ─────────────────▶ 링크로 연결
```

### 설계 결정

| 결정 | 이유 |
|---|---|
| **EvalHub job 으로 GuideLLM 실행** (GuideLLM 을 단독 Job 으로 돌리지 않음) | EvalHub 는 GA 다. 결과가 MLflow 와 Dashboard 에 자동으로 모이고 tenant RBAC 로 권한을 관리할 수 있다. 품질 평가(`../../evaluation/`)와 같은 도구를 쓴다 |
| **커스텀 provider `kbl_perf` (adapter v0.7.2) 사용** | built-in `guidellm`(v0.5.4)은 MLflow 에 지표를 남기지 않고, KB Life 업무 시나리오와 SLA 를 benchmark 로 정의할 수 없다 |
| **Raw 와 MaaS 모두 같은 모델 `qwen3-1-7b` 를 대상으로** | 두 매니페스트(`rhoai-serving-gitops/ai/raw/qwen3-1-7b.yaml`, `ai/maas/qwen3-1-7b.yaml`)의 vLLM 인자와 리소스가 같다. 그래서 경로 오버헤드(Gateway, 인증, 스케줄러)만 비교할 수 있다 |
| **실제 서비스 경로로 측정** | 사이징 검증 결과를 운영에 적용하려면 고객이 실제로 호출하는 경로의 지연이 들어가야 한다 |
| **MaaS 는 성능 테스트 전용 구독 사용** | 기존 구독(사용자당 20000 tokens/1m)으로는 몇 초 만에 429 가 난다. 인증을 끄는 annotation 은 문서가 운영 사용을 금지한다. 전용 구독을 쓰면 실제 경로를 유지하면서 한도만 높일 수 있다 |
| **시나리오와 대상은 순차 실행** | 한 job 의 benchmark 는 동시에 실행된다. SNO 의 GPU 1장(RTX 3060)을 time-slicing 으로 Raw 와 MaaS 가 나눠 쓰므로 동시에 측정하면 서로 간섭한다 |

## 4. 요구별 구성

### R1 부하/성능 테스트
- **시나리오**: [scenarios.yaml](../scenarios.yaml) 의 업무 시나리오 5종(`chat_baseline`, `chat_peak`, `rag_consult`, `doc_summary`, `capacity`)을 그대로 쓴다. 토큰 길이는 운영 지표로 정한다([BEST-PRACTICES.md](../BEST-PRACTICES.md) §2, §6).
- **대상 정의**: 새 파일 `targets.yaml` 에 대상별 URL, model 이름, 인증 방식, experiment 이름을 둔다. `run.py` 가 대상 × 시나리오를 순서대로 실행한다.
- **사이징 검증 절차**
  1. **목표 동시성 계산**: 목표 요청률 × 평균 E2E 시간 ([BEST-PRACTICES.md](../BEST-PRACTICES.md) §7)
  2. **포화점 측정**: replica 1개에 `capacity`(throughput) 와 `concurrent` 의 rate 단계를 올려 가며 측정한다. SLA(p95 TTFT 등)를 지키는 최대 동시성 C₁ 을 찾는다
  3. **서버 지표 확인**: 같은 시간대의 KV cache 사용률, waiting 요청 수, GPU 사용률을 본다. 병목이 GPU 인지, KV cache 인지, 큐인지 구분한다
  4. **필요 replica 계산**: ⌈목표 동시성 ÷ C₁ × 여유율(1.3)⌉ 을 계획한 replica 와 GPU 수와 비교한다
  5. **경로 비교**: Raw 와 MaaS 결과의 차이로 Gateway 와 인증 경로의 오버헤드를 확인한다

### R2 결과 확인

| 대상 | 방법 | 비고 |
|---|---|---|
| 운영자, AI팀 | **Dashboard Evaluations** 화면 | TP. tenant `kbl-eval` 프로젝트에서 본다 |
| 분석 | **MLflow** experiment `kbl-perf-raw`, `kbl-perf-maas` | run 비교, `benchmarks.json` 분포 |
| 보고 | **`report.py` 리포트** (md, csv) | SLA 통과 여부(p95 기준), Raw vs MaaS 비교, MLflow, Dashboard, 대시보드 링크 |

### R3 테스트 중 리소스 모니터링
- `observability/` 의 대시보드를 그대로 쓴다. 이 저장소에서 대시보드를 새로 만들지 않는다.
  - `kbl-model-serving`: vLLM 의 TTFT, ITL, E2E, running/waiting, KV cache, 토큰 처리량
  - `kbl-maas-usage`: MaaS 토큰 사용량, 429 횟수
  - RHOAI Observe & monitor 의 Models / LLM 탭, Grafana `dcgm`: GPU 사용률과 메모리
- 리포트가 job 의 시작과 종료 시각으로 **시간 범위가 지정된 대시보드 URL** 을 만들어 준다. 결과와 리소스 상태를 같은 시간대로 바로 볼 수 있다.
- GuideLLM 이 측정한 값(클라이언트 측)과 vLLM 지표(서버 측)를 같이 보면 네트워크와 경로 지연을 구분할 수 있다.

### R4 고객 프롬프트 (선택)
- 비식별 처리한 프롬프트 파일(jsonl)을 S3(rustfs, Secret `kbl-eval-s3`) 나 PVC 에 둔다.
- job 의 `test_data_ref` 로 `/test_data` 에 마운트하고 GuideLLM `data` 에 파일 경로를 지정한다.
- 개인정보가 들어간 원문은 쓰지 않는다. 비식별 처리는 고객사가 하고, 저장소에는 형식 예시만 둔다.

## 5. 구현 항목

| # | 항목 | 변경 위치 | 확인 방법 | 리스크 / 확인 필요 |
|---|---|---|---|---|
| 1 | **대상 정의와 실행기 확장**: `targets.yaml` 추가, `run.py` 를 대상 × 시나리오로 실행, `config.env` 의 `model-hub` 기본값 교체 | `eval/guidellm/` | `run.sh` 를 짧게(`MAX_SECONDS=30`) 실행해 대상별 job 이 `completed` 되는지 확인 | - |
| 2 | **Raw 경로 연결**: 8443 + SA 토큰([문서] 2.16), `kbl-eval` SA 에 `model-caller` Role 바인딩, `kbl-eval` namespace 에 `kblife.co.kr/model-consumer=true` 레이블 | `rhoai-serving-gitops/ops/agent/` 등, `eval/evaluation/evalhub/tenant/` | 작은 job 으로 200 응답 확인 | **sidecar 가 service CA 를 신뢰하지 않는 문제(PoC 필요).** 해결되지 않으면 8080 을 쓴다. 그러려면 `ops/kbl-models/36-netpol-ingress.yaml` 에 `kbl-eval` → 8080 을 열어야 하고 인증을 거치지 않게 된다(측정 한계로 기록) |
| 3 | **MaaS 경로 연결**: 성능 테스트 전용 그룹 또는 SA, MaaSAuthPolicy, 높은 `tokenRateLimits` 의 MaaSSubscription, API key Secret(SealedSecret), job 에 `model.auth.secret_ref` | `rhoai-serving-gitops/ops/maas-policy/`, `eval/evaluation/evalhub/tenant/` | 작은 job 으로 200 응답과 429 미발생 확인 | `maas.apps` 인증서(ingress 기본 인증서)를 sidecar 가 신뢰하는지(PoC). API key 만료 정책. Argo CD 관리 리소스라 Git 으로만 변경 |
| 4 | **결과 리포트 개선**: MLflow artifact `benchmarks.json` 에서 p50/p95 를 가져와 판정(pod 로그 의존 제거), Raw vs MaaS 비교 표, MLflow, Dashboard, 대시보드 링크 출력 | `eval/guidellm/report.py`, `scenarios.yaml`(SLA 를 p95 로) | 끝난 job 의 결과 폴더로 `report.py` 를 다시 실행 | MLflow artifact 경로와 인증 확인. `provider-kbl-perf.json` 의 `pass_criteria`(서버 판정, 평균값)와 리포트 판정(p95)이 다르다는 점을 리포트에 명시 |
| 5 | **모니터링 연계**: 리포트에 시간 범위가 지정된 Perses/Grafana URL. 필요하면 `kbl-model-serving` 에 성능 테스트용 변수나 패널 추가 | `eval/guidellm/report.py`, (`observability/` 쪽 작업) | 링크를 열어 해당 시간대 그래프 확인 | 대시보드 변경은 `observability/` 에서 한다 |
| 6 | **고객 프롬프트 (선택)**: 프롬프트 파일 형식 예시, `test_data_ref` 사용 시나리오 | `eval/guidellm/` | 예시 파일 10건으로 job 실행 | **adapter v0.7.2 가 `/test_data` 파일을 `data` 로 받는지, 폐쇄망 tokenizer(`processor`) 가 동작하는지 PoC 필요** |
| 7 | **기존 문서 정정** | `README.md`, `BEST-PRACTICES.md`, `basic_job.py`, `report.py`, `provider-kbl-perf.json` | 검토 | 아래 목록 |

**항목 7 정정 대상**
- `README.md` 구성도: `provider-kbl-perf.json` 을 "시스템 GuideLLM 이미지 재사용"이라고 적었지만 실제로는 v0.7.2 community 이미지(digest)다.
- `basic_job.py` docstring: built-in `guidellm` 을 쓴다고 적었지만 코드는 `kbl_guidellm` 을 쓴다.
- `BEST-PRACTICES.md` 와 `report.py` docstring: built-in adapter 를 v0.5.0 으로 적었다. 클러스터 실측은 v0.5.4 다. "MLflow 지표 없음" 설명은 v0.7.2 에서는 맞지 않는다.
- `kbl_perf`(256Mi) 와 `kbl_guidellm`(128Mi) 의 memory request 가 다르다.
- 권장(p95)과 판정(평균)이 다르다(항목 4 에서 해결).

### 진행 순서

1. **PoC** (항목 2, 3 의 연결성): 클러스터 변경이 있으므로 실행 전에 확인을 받는다.
2. 항목 1, 2, 3 구현과 짧은 측정
3. 항목 4, 5
4. 항목 6 (선택), 항목 7

## 6. 제약과 리스크

- **테스트 클러스터 한계**
  - GPU 는 RTX 3060 12GiB 한 장을 time-slicing 2개로 나눠 쓴다.
  - 측정값은 절차를 확인하는 용도다. 고객 환경(DGX B300, MIG)의 사이징 수치로 쓰지 않는다.
  - 측정하는 동안에는 같은 GPU 를 쓰는 다른 워크로드(guardrails self-check 등)를 멈춘다.
- **community 이미지**: GuideLLM adapter 이미지는 `quay.io/evalhub/community-guidellm` 이다. 고객 환경에서는 Red Hat 지원 범위를 확인하고 내부 레지스트리(Nexus)로 미러링한다.
- **이미지 크기**: 처음 pull 할 때 SNO 노드에 DiskPressure 가 생긴 적이 있다. 미리 pull 해 둔다.
- **지원 수준**
  - EvalHub 는 GA 다.
  - Dashboard Evaluations 화면, 플랫폼 관측 대시보드, MaaS 사용량 대시보드는 **TP** 다.
  - llm-d Perses 대시보드는 GA 다.
- **측정 시간**: 30초 측정은 경향을 보는 용도다. 판정에는 `MAX_SECONDS=120` 이상을 쓴다.
- **Argo CD 관리 리소스**: EvalHub CR, provider ConfigMap, serving 매니페스트, MaaS 정책은 Git 으로만 바꾼다.
