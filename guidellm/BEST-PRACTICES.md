# LLM 서빙 부하 테스트 베스트 프랙티스 (RHOAI 3.5, KB Life)

RHOAI에서 GuideLLM 같은 부하 테스트를 할 때 따를 원칙과 체크리스트다.
RHOAI 3.5 공식 문서에는 부하 테스트 운영 가이드가 없다. 이 문서는 일반적인 LLM 서빙 성능 측정 원칙에 RHOAI 구성요소와 이 리포 lab에서 실측한 제약을 결합해 정리했다.

> 근거 환경(2026-11): RHOAI 3.5.2, EvalHub(GA) + GuideLLM provider(커뮤니티 이미지 v0.5.0), MLflow, Kueue operator 1.3.2, Custom Metrics Autoscaler(KEDA) 2.19, OpenShift Pipelines 1.24, user-workload-monitoring 활성, NVIDIA GPU Operator(DCGM exporter), Models-as-a-Service gateway

---

## 1. 목적을 먼저 정한다

부하 테스트는 "얼마나 빠른가"가 아니라 **무엇을 결정하려는가**에서 시작한다.

| 목적 | 묻는 질문 | GuideLLM 프로파일 | 이 리포 시나리오 |
|---|---|---|---|
| 기준선 | 1명일 때 지연은? | `synchronous` | `chat_baseline` |
| SLA 검증 | 피크 동시 사용자에서 목표를 지키나? | `concurrent`(동시 N명), `poisson`(불규칙 도착) | `chat_peak`, `rag_consult`, `doc_summary` |
| 용량 한계 | 1대가 최대 얼마를 처리하나? | `throughput`(rate = 동시 상한), `sweep` | `capacity` |
| 회귀 비교 | 모델·설정 변경 후 느려졌나? | 같은 시나리오를 변경 전후로 실행 | 전체를 짧게 |

- `sweep`은 저부하부터 포화까지 자동으로 훑기 때문에 탐색용으로 좋다. 다만 EvalHub adapter v0.5.0은 **첫 단계 결과만 지표로 보고**한다. 그래서 판정용으로는 단일 단계 프로파일을 쓴다.

## 2. 실제 트래픽처럼 만든다

- **토큰 길이는 측정한다.** 운영 서빙의 vLLM 히스토그램 `vllm:request_prompt_tokens`와 `vllm:request_generation_tokens`에서 업무별 중앙값과 p90을 구한다(아래 6절 PromQL).
- **RAG 입력**은 "시스템 프롬프트 + 검색 청크 수 × 청크 길이 + 대화 이력 + 질문"으로 계산한다. 예: 300 + 4 × 400 + 200 + 50 ≈ 2,150토큰. 이 리포의 `rag_consult`가 2,048토큰인 이유다.
- **합성 → 실제 순서로 한다.** 처음에는 `data: prompt_tokens=N,output_tokens=M` 합성 데이터로 시작한다. 최종 검증은 **비식별 처리한 실제 프롬프트 샘플**(GuideLLM `data`에 파일·데이터셋 지정)로 한다.
- **프롬프트 다양성을 유지한다.** 같은 프롬프트를 반복하면 prefix cache 적중으로 TTFT가 비현실적으로 좋아진다. `vllm:prefix_cache_hits_total`이 운영 수준인지 확인한다.

## 3. 실제 서비스 경로로 측정한다

사용자 체감 성능은 vLLM 단독 성능이 아니다. 다음 두 가지를 **둘 다** 측정해 구간별 오버헤드를 분리한다.

| 측정 경로 | 용도 | 이 리포 |
|---|---|---|
| vLLM 직접 (`<isvc>-metrics:8080`) | 모델·런타임 튜닝의 기준선 | `guidellm/`(EvalHub) |
| 실제 서비스 경로: Route/MaaS Gateway → (NeMo 가드레일) → kube-rbac-proxy(8443) → vLLM | SLA 판정, 사용자 체감 | 미구현, 아래 참고 |

- **EvalHub 제약(lab 확인):** job sidecar의 모델 프록시는 OpenShift service CA를 신뢰하지 않는다. 그래서 8443(kube-rbac-proxy)을 호출할 수 없다. 서비스 경로 측정은 GuideLLM CLI를 직접 실행한다. 실행 위치는 다음 중 하나다.
  - 워크벤치 또는 일회성 Job: `pip install guidellm`, `SSL_CERT_FILE`에 service CA, 토큰 헤더 지정
  - Tekton Task: 9절의 배포 파이프라인
- **가드레일 오버헤드:** 가드레일을 거치는 경로는 별도로 측정한다. NeMo self-check는 요청 1건에 LLM을 최대 3번 호출하므로 지연과 GPU 부하가 크게 는다.

## 4. 환경을 격리하고 운영과 같게 맞춘다

- **운영과 같게(parity):**
  - GPU 종류와 수, HardwareProfile
  - vLLM 인자: `--max-model-len`, `--max-num-seqs`, `--gpu-memory-utilization`, 양자화, `--enable-prefix-caching`, tensor parallel
  - 레플리카 수, 오토스케일 설정
  - 런타임 이미지 digest
- **격리:**
  - 측정 대상 GPU를 다른 부하(평가 job, 가드레일 self-check, 다른 모델)와 공유하지 않는다. 이 lab은 GPU 1장을 공유하므로 측정 중 다른 부하를 멈췄다.
  - 전용 노드에서 측정한다. HardwareProfile과 taint/toleration을 쓴다.
  - 부하 생성기(GuideLLM pod)는 GPU 노드의 CPU를 빼앗지 않도록 별도 노드에 두거나 리소스를 제한한다.
- **직렬화:**
  - EvalHub는 한 job의 벤치마크를 **동시에** 실행한다(lab 확인). 그래서 시나리오는 job을 하나씩 순서대로 제출한다(`run.py`).
  - 여러 팀이 쓰는 클러스터에서는 Kueue로 평가·부하 job이 동시에 뜨지 않게 큐를 둔다. EvalHub job 요청의 `queue: {kind: kueue, name: ...}`(TP)를 쓴다.

## 5. 측정 방법

| 항목 | 권장 | 비고 |
|---|---|---|
| 워밍업 | 앞 10~30초 제외 | adapter v0.5.0은 **초 단위**다. `"10%"`는 10초로 처리된다 |
| 측정 시간 | 판정용 2~5분 이상, 요청 수백 건 이상 | 30초는 경향 확인용이다. 이 lab의 synchronous 30초는 요청 5건뿐이었다 |
| 반복 | 같은 조건 3회 이상 | 중앙값을 쓰고 편차(최대-최소)도 함께 보고한다 |
| 판정 지표 | **p95/p99** (TTFT, E2E) | 평균은 꼬리 지연을 숨긴다 |
| 부하 증가 | 동시 1 → 2 → 4 → 8 → 16 … | 지연이 급증하는 **무릎 지점**을 찾는다 |
| 오류 | 오류율 0%를 기본으로 | 오류가 생기는 부하가 사실상의 한계다 |

SLA 예시(업무별로 합의해 정한다):

| 업무 | 지표 | 예시 목표 |
|---|---|---|
| 상담 챗봇(스트리밍) | TTFT p95 | ≤ 1.5초 |
| 상담 챗봇(스트리밍) | ITL 평균 | ≤ 50ms (사용자당 초당 20토큰 이상) |
| 요약·분류(비스트리밍) | E2E p95 | ≤ 20초 |
| 전체 | 오류율 | 0% |

## 6. 서버 측 지표를 함께 본다

클라이언트(GuideLLM) 숫자만으로는 **왜** 느린지 알 수 없다.
이 클러스터는 user-workload-monitoring이 켜져 있고 모델용 ServiceMonitor(`qwen3-4b-instruct-2507-v10-metrics`)가 있다. 그래서 OpenShift 콘솔 Observe → Metrics나 대시보드 observability에서 아래 지표를 바로 조회할 수 있다(메트릭 이름은 서빙 중인 vLLM에서 확인).

| 보는 것 | PromQL | 해석 |
|---|---|---|
| 큐잉 | `vllm:num_requests_waiting` | 0보다 크게 계속 유지되면 동시 처리 한계를 넘은 것 |
| 동시 처리 | `vllm:num_requests_running` | `--max-num-seqs`에 붙어 있으면 상한에 도달 |
| KV cache 포화 | `vllm:kv_cache_usage_perc` | 0.9 이상이면 긴 입력·동시성 한계 |
| 선점 | `rate(vllm:num_preemptions_total[1m])` | 0보다 크면 KV cache 부족으로 요청이 밀려남 |
| 대기 시간 p95 | `histogram_quantile(0.95, sum by (le)(rate(vllm:request_queue_time_seconds_bucket[1m])))` | TTFT 중 대기 비중 |
| prefill 시간 p95 | `histogram_quantile(0.95, sum by (le)(rate(vllm:request_prefill_time_seconds_bucket[1m])))` | 긴 입력 병목 |
| TTFT p95(서버) | `histogram_quantile(0.95, sum by (le)(rate(vllm:time_to_first_token_seconds_bucket[1m])))` | 클라이언트 TTFT와 비교 |
| 입력 토큰 분포 | `histogram_quantile(0.5, sum by (le)(rate(vllm:request_prompt_tokens_bucket[1h])))` | 2절 시나리오 길이 산정 |
| GPU 사용률 | `DCGM_FI_DEV_GPU_UTIL` | 낮은데 지연이 크면 GPU 밖(프록시, 네트워크, CPU)이 병목 |

- 클라이언트 TTFT가 서버 TTFT보다 크게 높으면 그 차이는 네트워크, 라우터, 인증 프록시, 가드레일 구간에서 생긴 것이다.

## 7. 결과를 용량과 설정에 반영한다

**용량 산정 예 (이 lab 실측 기반):**
- `chat_peak`(동시 8명)는 TTFT 평균 545ms로 SLA를 만족했다. `capacity`(동시 16 상한)에서는 TTFT p95가 1.4초였다.
  - 따라서 이 GPU 1장이 TTFT p95 1.5초 SLA 안에서 감당하는 동시 요청은 약 16건이다.
- 피크 시간 상담 요청이 시간당 6,000건이고 1건 응답에 평균 8초가 걸린다고 하자.
  - 동시 요청 = 6,000 ÷ 3,600 × 8 ≈ 13.3건이다.
  - 필요 레플리카 = 13.3 ÷ 16 × 1.3(여유 30%) ≈ 1.1, 즉 **2대**다(장애 대비 최소 2대).

**증상별 조치:**

| 증상 | 원인 | 조치 |
|---|---|---|
| 동시 사용자 증가에 따라 TTFT 급증, `num_requests_waiting` 증가 | 큐잉 | `--max-num-seqs` 상향(KV 여유 시), 레플리카 추가 |
| `kv_cache_usage_perc` 높음, 선점 발생 | KV cache 부족 | `--gpu-memory-utilization` 상향, `--max-model-len` 축소, 양자화(KV cache FP8), GPU 추가 |
| 긴 입력에서 TTFT 큼 | prefill 병목 | RAG 청크 수·길이 축소, prefix caching, llm-d(prefill/decode 분리) 검토 |
| ITL 증가 | decode 경합 | 동시성 제한, 레플리카 추가 |
| GPU 사용률 낮은데 지연 큼 | GPU 밖 병목 | 라우터·프록시·가드레일 구간 측정(3절) |

**오토스케일:**
- KServe와 KEDA(Custom Metrics Autoscaler, 설치됨)를 쓴다.
- 기준은 CPU가 아니라 `vllm:num_requests_waiting`이나 `vllm:num_requests_running` 같은 Prometheus 지표로 잡는다.
- 임계값은 부하 테스트의 무릎 지점보다 낮게 둔다(예: 대기 요청 > 0이 30초 지속되면 확장). 모델 기동 시간(이 lab 약 2~3분)을 고려해 최소 레플리카를 둔다.

## 8. 기록하고 재현 가능하게 만든다

- **결과와 함께 남길 것:**
  - 모델과 런타임 이미지 digest, vLLM 인자, GPU 종류와 수, 레플리카
  - GuideLLM 버전, 프로파일, rate, 토큰 길이, 측정 시간, warmup
  - 측정 경로(직접 / 서비스 경로), 측정 시각, 동시에 돌던 부하 여부
- **저장 위치:**
  - EvalHub job 이력과 MLflow experiment(`kbl-qwen3-4b-perf`)
  - 원본 `benchmarks.json`은 EvalHub `exports.oci`나 S3로 보관한다.
  - adapter v0.5.0은 성능 지표와 원본 결과를 MLflow에 남기지 않는다. 그래서 `run.py` 이후 MLflow에 직접 기록하는 보완이 필요하다.
- **같은 시나리오 파일을 계속 쓴다.** 수치는 같은 조건끼리만 비교할 수 있다. `scenarios.yaml`을 Git으로 관리하고, 바꾸면 버전을 남긴다.

## 9. 배포 파이프라인에 넣는다 (품질 + 성능 게이트)

모델, 런타임, 설정이 바뀔 때 자동으로 실행한다(OpenShift Pipelines 또는 AI Pipelines).

```
모델/설정 변경(Git) → 스테이징 배포 → 품질 평가(EvalHub: 골든셋, KMMLU)
                                    → 성능 평가(핵심 시나리오 2~3개, 각 1~2분)
                                    → 게이트 판정 → 운영 배포 / 중단
```
- 게이트 예: `golden_score ≥ 0.6` **그리고** `chat_peak TTFT p95 ≤ 1.5s` **그리고** 오류율 0%
- 회귀 기준을 함께 건다. 직전 운영 버전보다 TTFT p95가 20% 이상 나빠지면 중단한다.
- 분기마다, 그리고 GPU나 레플리카를 바꿀 때 전체 용량 테스트를 다시 한다.

## 10. 보안·거버넌스

- 부하 테스트 프롬프트에 **실제 고객 개인정보를 쓰지 않는다.** 합성 데이터나 비식별 데이터를 쓴다.
- 테스트 전용 namespace, 계정, 토큰을 쓴다. 이 리포는 `kbl-eval` 테넌트와 `kbl-eval-runner` SA를 쓴다. 토큰은 짧게 발급한다(`oc create token --duration`).
- 운영 클러스터에서 측정할 때:
  - 시간대를 공지한다.
  - 전용 레플리카나 별도 InferenceService로 사용자 트래픽과 분리한다.
  - 중단 기준(오류율, 지연)을 미리 정한다.
- GuideLLM provider 이미지(`quay.io/evalhub/community-guidellm`)는 커뮤니티 이미지다. Red Hat 지원 범위를 확인하고, 폐쇄망에서는 사내 레지스트리로 미러링한다.

---

## 체크리스트

**측정 전**
- [ ] 목적(기준선 / SLA / 용량 / 회귀)과 판정 지표·목표값을 합의했다
- [ ] 업무별 토큰 길이를 운영 지표에서 산정했다
- [ ] 대상 환경이 운영과 같다(GPU, vLLM 인자, 레플리카, 이미지 digest)
- [ ] 같은 GPU를 쓰는 다른 부하를 멈췄다. 평가 job이 동시에 돌지 않는다
- [ ] 측정 경로를 정했다(직접 / 서비스 경로 / 가드레일 포함)
- [ ] 서버 지표 대시보드를 준비했다(6절)

**측정 중**
- [ ] 워밍업 이후 정상상태에서 측정하고 있다
- [ ] `num_requests_waiting`, `kv_cache_usage_perc`, 선점, GPU 사용률을 관찰하고 있다
- [ ] 오류가 생기면 중단하고 원인을 기록한다

**측정 후**
- [ ] 3회 이상 반복해 편차를 확인했다
- [ ] p95 기준으로 SLA를 판정했다
- [ ] 무릎 지점과 1대당 수용량을 산정했다
- [ ] 환경 정보와 결과를 함께 기록했다(8절)
- [ ] 조치(설정 변경, 레플리카, 오토스케일 임계값)를 결정했다

## 이 lab에서 확인한 제약 (RHOAI 3.5, GuideLLM adapter v0.5.0)

| 제약 | 영향 | 대응 |
|---|---|---|
| 한 job의 벤치마크가 동시에 실행됨 | 시나리오 간 부하 간섭 | 시나리오마다 job을 차례로 제출 (`run.py`) |
| sweep 등 여러 단계 프로파일은 첫 단계만 지표로 보고 | 판정에 쓸 수 없음 | 단일 단계 프로파일로 시나리오를 나눔 |
| EvalHub로는 평균값만 보고, `benchmarks.json`은 업로드되지 않음 | p95 판정 불가, MLflow 지표 없음 | pod 로그의 요약 표를 파싱 (`report.py`), OCI export 사용 |
| `warmup: "10%"`가 10초로 처리됨 | 측정 시간이 줄어듦 | 초 단위로 지정 (`WARMUP=5`) |
| throughput 프로파일에 rate 필수 | 없으면 실패 | 동시 상한 rate를 지정 |
| sidecar 모델 프록시가 service CA를 신뢰하지 않음 | 8443(kube-rbac-proxy) 측정 불가, 8080 측정값이 낙관적 | 서비스 경로는 GuideLLM CLI로 별도 측정 |
