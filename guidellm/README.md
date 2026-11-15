# 서빙 성능 평가 예제 (EvalHub + GuideLLM)

KB Life 업무 시나리오별로 **LLM 서빙 성능**(응답 속도, 처리량, 동시 사용자 수용량)을 측정하고 SLA 통과 여부를 표로 보여준다.
RHOAI 3.5 EvalHub 에서 **GuideLLM** adapter 로 부하를 준다. 대상은 `targets.yaml` 의 **Raw(KServe RawDeployment)** 와 **MaaS(llm-d)** 모델이고,
고객이 실제로 호출하는 경로(kube-rbac-proxy, MaaS Gateway·인증·rate limit)를 그대로 거친다.

> 운영에서 부하 테스트를 설계·수행하는 원칙과 체크리스트는 [BEST-PRACTICES.md](BEST-PRACTICES.md)를 본다.

> RHOAI 3.5 가 기본 제공하는 EvalHub·GuideLLM 기능과 연동 기능은 [docs/01-capabilities.md](docs/01-capabilities.md),
> Raw/MaaS 모델 부하 테스트와 사이징 검증 아키텍처(구현 계획 포함)는 [docs/02-architecture.md](docs/02-architecture.md)를 본다.

> 이 예제는 **속도와 용량**만 본다. 답이 맞는지(품질)는 `../evaluation/`(KMMLU, 골든셋)에서 평가한다.
> 같은 모델이라도 GPU, vLLM 설정, 레플리카 수, 양자화 여부에 따라 결과가 달라진다. 그래서 이 수치는 모델 자체가 아니라 **서빙 환경**의 성능이다.

## 구성

```
guidellm/
├── config.env               # 부하 설정 (측정 시간, 부하 배수, 대상·시나리오 선택, MLflow·Grafana 주소)
├── targets.yaml             # 대상 모델 (raw, maas): URL, 인증 Secret, tokenizer, MLflow experiment, 대시보드 링크
├── scenarios.yaml           # KB Life 업무 시나리오 (토큰 길이 또는 고객 프롬프트 파일, 부하 방식, SLA 기준)
├── prompts-example.jsonl    # 고객 프롬프트 파일 형식 예시 (선택 시나리오 customer_prompts)
├── provider-kbl-perf.json   # EvalHub provider 정의 (GuideLLM adapter v0.7.2 community 이미지, digest 고정)
├── register-provider.sh     # provider 를 REST API 로 테넌트에 등록/갱신
├── setup-targets.sh         # 대상 접속용 Secret(kbl-perf-raw, kbl-perf-maas) 생성
├── run.sh / run.py          # 대상 × 시나리오를 하나씩 차례로 실행
├── report.py                # 결과 → SLA 리포트 (p95 판정, 대상 비교, 대시보드 링크 / Markdown·CSV)
├── docs/                    # 기능 정리(01), 아키텍처(02)
└── results/<시각>/          # 요청·결과·benchmarks.json, sla-report.md, sla-report.csv
```

- `../evaluation/`이 배포한 EvalHub 서버, 테넌트 `kbl-eval`, 모델 접근 권한을 **함께 사용**한다. 소스와 매니페스트는 공유하지 않는다.
- provider `kbl_perf`는 EvalHub CR(ConfigMap)을 고치지 않도록 **REST API로 테넌트에 등록**한다. `run.sh`가 자동으로 등록하거나 갱신한다.
- 시나리오를 한 job에 몰아 넣으면 EvalHub가 pod를 **동시에** 띄워 서로 부하를 간섭한다. 그래서 `run.py`는 (대상, 시나리오)마다 job을 하나씩 제출하고, 끝나기를 기다렸다가 다음을 실행한다.

## 대상 모델과 접속 경로 (`targets.yaml`)

| 대상 | URL | 인증 | 필요한 설정 |
|---|---|---|---|
| `raw` | `https://qwen3-1-7b-predictor.kbl-models.svc.cluster.local:8443/v1` | kube-rbac-proxy. sidecar 가 job SA(`evalhub-redhat-ods-applications-job`) 토큰을 넣는다 | Secret `kbl-perf-raw`(`ca_cert`=service CA), RoleBinding `kbl-models/caller-kbl-eval`(rhoai-serving-gitops `ops/agent/kbl-eval.yaml`), `kbl-eval` 라벨 `kblife.co.kr/model-consumer` |
| `maas` | `https://maas.apps.rhoai-35.edwin.home/v1` | MaaS API key (`sk-oai-…`) | Secret `kbl-perf-maas`(`api-key`, `ca_cert`=인그레스 CA), 성능 테스트 전용 구독 `kbl-perf-testers`(rhoai-serving-gitops `ops/maas-policy/`) |

- job 은 `model.auth.secret_ref` 로 Secret 을 받는다. sidecar 는 Secret 의 **`ca_cert` 로 TLS 를 검증**하고, `api-key` 가 있으면 그 값을, 없으면 job SA 토큰을 Bearer 로 보낸다.
  `ca_cert` 를 주지 않으면 시스템 CA 만 신뢰해 service CA·사설 인그레스 CA 엔드포인트에서 x509 오류가 난다(예전에 8080 을 쓴 이유).
  - 문서 근거: *Evaluating AI systems* 2.15(API key), 2.16(SA 토큰). `ca_cert` 키는 문서에 없고 EvalHub sidecar 소스(`eval-runtime-sidecar` 의 model client)에서 확인했다(3.5 문서 미기재).
- MaaS 는 일반 구독(사용자당 2만 tokens/분)으로는 부하 테스트 중 429 가 나므로, 한도를 크게 둔 **성능 테스트 전용 구독**의 사용자가 발급한 key 를 쓴다.

```bash
./setup-targets.sh raw                          # 처음 한 번
MAAS_API_KEY=sk-oai-... ./setup-targets.sh maas # kbl-perf-testers 그룹 사용자가 발급한 key (Dashboard → API keys)
```

## 처음 이해할 때: 단일 파일 예제

`basic_job.py`는 설정 파일이나 시나리오 파일 없이, EvalHub provider **`kbl_guidellm`**으로 job 하나를 제출하고 대기한 뒤 결과를 출력하는 예제다. EvalHub Python SDK를 쓰고, provider는 새로 만들지 않는다. 결과는 MLflow에도 남는다. URL과 부하는 파일 맨 위에서 직접 고친다.
- 전제: EvalHub CR(`evaluation/evalhub/hub/evalhub.yaml`)의 `spec.providers`에 `kbl-guidellm`이 켜져 있어야 한다. CRD 기본값(`garak`, `garak-kfp`, `lm-evaluation-harness`)에는 GuideLLM이 없다.
- `kbl_guidellm`은 시스템 provider `guidellm`과 벤치마크 정의가 같고, adapter 이미지만 시스템 버전(v0.5.x)에서 **v0.7.2**로 바꾼 커스텀 provider다(`evaluation/evalhub/hub/provider-kbl-guidellm.yaml`).
- 벤치마크 id는 provider에 정의된 것(`concurrent`, `throughput`, `sweep`, `quick_perf_test` 등)을 쓴다. 실제 부하는 job `parameters`(`profile`, `rate`, `max_seconds`, `data`)가 정한다.
```bash
uv run --with "eval-hub-sdk[client]==1.0.7" python guidellm/basic_job.py
```
- SDK는 EvalHub 서버와 같은 메이저·마이너(1.0.x)를 쓴다. RHOAI 3.5 서버는 1.0.0이고, SDK 1.0.0과 1.0.7로 동작을 확인했다. 서버 버전은 `oc logs -n redhat-ods-applications deploy/evalhub -c evalhub | grep "API Server starting"`로 확인한다.
- `EvalHubClient`는 비동기다. 스크립트에서는 `SyncEvalHubClient`를 쓴다.
- SDK 응답 모델은 서버의 `test`(PASS/FAIL) 필드를 버리므로, 판정은 파일 안의 기준값으로 계산한다.
- p95 요약 표는 `client.jobs.get_logs()`로 가져온다. `oc logs`가 필요 없다.

## MLflow에서 결과 보기

| provider | adapter 이미지 | MLflow 기록 |
|---|---|---|
| `guidellm` (RHOAI 3.5 시스템) | v0.5.4 (2026-11-14 클러스터 확인) | 없음(v0.5.0 에서 확인). EvalHub 서버가 만든 빈 job run(RUNNING)과 `evaluation-card.json`만 남는다 |
| `kbl_guidellm`, `kbl_perf` (이 리포) | v0.7.2 | 벤치마크별 run에 **Metrics**(요청/초, 토큰/초, TTFT·ITL 평균)와 **Artifacts**(`benchmarks.json`, `.csv`, `.html`, p95 등 분포와 차트 리포트 포함)가 남는다 |

- job 요청에 `experiment`가 있어야 기록된다. 없으면 adapter가 MLflow 기록을 건너뛴다.
- 보는 곳: RHOAI 대시보드 → MLflow → workspace `kbl-eval` → experiment(`kbl-perf-raw`, `kbl-perf-maas`, `guidellm_basic_<모델>`). 예전 결과는 `kbl-qwen3-4b-perf`.
- Dashboard → Develop & train → **Evaluations**(프로젝트 `kbl-eval`)에서도 job 목록과 결과를 본다(TP, OdhDashboardConfig `disableLMEval: false` 필요).
  - 이름에 `<job-id>_0`처럼 번호가 붙은 run이 벤치마크 결과다.
  - job 이름 그대로인 run은 EvalHub 서버가 만든 요약 run이며 `evaluation-card.json`만 있다.
- run을 여러 개 선택해 Compare 하면 실행 간 지표를 나란히 비교할 수 있다.
- v0.7.2는 RHOAI 3.5 기본 제공 버전보다 새로운 커뮤니티 이미지다. RHOAI를 업그레이드할 때 시스템 provider와 다시 비교한다.
- artifact 구성은 `outputs` 파라미터에 따라 다르다. `basic_job.py`는 지정하지 않아 기본값(json, csv, html)이 되고, `run.sh`는 `json,csv,yaml`을 지정한다. HTML 리포트는 템플릿을 인터넷에서 받으므로 폐쇄망에서는 빼야 한다.
- 새 이미지를 처음 받을 때 GuideLLM 이미지가 수 GB라 SNO 디스크가 임계치를 넘을 수 있다. 이 lab에서는 v0.7.2를 받은 직후 DiskPressure taint가 걸려 다음 job이 1분 정도 스케줄링되지 못했다. 쓰지 않는 이미지 버전은 정리한다.

## 실행

```bash
./run.sh                                    # 기본: 대상 2개(raw, maas) × 시나리오 5개, 각 30초 (전체 약 10분)
TARGETS=raw ./run.sh                        # 대상 하나만
MAX_SECONDS=60 ./run.sh                     # 측정 시간 변경 (모든 시나리오에 우선 적용)
SCENARIOS=chat_baseline,chat_peak ./run.sh  # 일부 시나리오만
RATE_SCALE=0.5 ./run.sh                     # 동시 사용자 수를 절반으로
MAX_REQUESTS=20 ./run.sh                    # 요청 수로 제한 (시간보다 먼저 끝날 수 있음)
```
필요한 도구는 `oc`(로그인 상태), `curl`, `jq`, `uv`다. 첫 실행 때는 GuideLLM 이미지(약 수 GB)를 받느라 5분 이상 더 걸린다.

설정 우선순위는 **환경변수 > `scenarios.yaml`의 시나리오별 값 > `config.env` 기본값**이다.

## 평가 지표

| 지표 | 의미 | KB Life에서 쓰는 곳 |
|---|---|---|
| **TTFT** (Time To First Token) | 질문 후 첫 글자가 나오기까지 걸리는 시간 | 상담 챗봇 체감 응답성. SLA 예: p95 ≤ 1.5초 |
| **ITL** (Inter-Token Latency) | 답변 토큰 사이의 간격. 1000 ÷ ITL = 사용자 1명이 받는 초당 토큰 | 스트리밍 가독성. 예: ≤ 50ms(초당 20토큰 이상) |
| **E2E 지연** | 요청부터 답변 완료까지 | 요약이나 분류처럼 스트리밍하지 않는 업무의 처리 시간 |
| **요청/초, 출력 토큰/초** | 서버 전체가 처리한 양 | 용량 산정: 필요 GPU 수 ≈ 피크 부하 ÷ 1대 처리량 |
| **최대 처리량** (`capacity`) | 포화될 때의 처리 한계 | 레플리카 수와 오토스케일 기준 |
| **완료/오류 요청** | 측정 중 성공·실패한 요청 수 | 과부하 시 안정성 |

지표 출처:
- 평균값은 EvalHub job 결과에서 온다. MLflow와 대시보드에서도 같은 값을 본다.
- 중앙값/p95, 완료/오류 요청 수, 측정 구간은 `report.py` 가 MLflow artifact `benchmarks.json`(adapter v0.7.2)에서 읽는다. 처음 한 번 받아 결과 폴더에 저장하므로 평가 pod 가 정리된 뒤에도 남는다.
- 리포트(`sla-report.md`)에는 대상 비교 표(Raw vs MaaS)와, 측정 구간(앞뒤 1분)이 지정된 Grafana 대시보드 링크(vLLM, GPU, MaaS 사용량)가 들어간다. 링크는 `targets.yaml` 의 `dashboards` 에서 정한다.
- 다시 만들기: `uv run --no-project --with pyyaml python -I report.py results/<시각>`

## 시나리오 (`scenarios.yaml`)

| 시나리오 | 업무 | 입력/출력 토큰 | 부하 | SLA 예시 |
|---|---|---|---|---|
| `chat_baseline` | 상담 챗봇, 1명 기준선 | 256 / 256 | synchronous (1명씩 순차) | TTFT p95 ≤ 500ms |
| `chat_peak` | 상담 챗봇, 동시 사용자 | 256 / 256 | concurrent ×8 | TTFT p95 ≤ 1500ms |
| `rag_consult` | RAG 상담 (약관 청크 포함 긴 입력) | 2048 / 256 | concurrent ×4 | TTFT p95 ≤ 3000ms |
| `doc_summary` | 약관·청약서 요약 | 4096 / 512 | concurrent ×2 | 출력 ≥ 초당 50토큰 |
| `capacity` | 최대 처리량 | 256 / 256 | throughput (동시 16 상한, 포화까지) | 요청 ≥ 초당 1건 |
| `customer_prompts` (선택, 주석) | 고객 프롬프트 파일 | 파일 | concurrent ×8 | TTFT p95 ≤ 1500ms |

### KB Life 업무에 맞추기
1. **토큰 길이**: 실제 서비스 로그(또는 vLLM `/metrics`의 `prompt_tokens`, `generation_tokens` 히스토그램)에서 업무별 평균 입력·출력 길이를 구해 `prompt_tokens`와 `output_tokens`에 넣는다. RAG는 "시스템 프롬프트 + 검색 청크 수 × 청크 길이 + 질문"으로 계산한다.
2. **동시 사용자(rate)**: 피크 시간대의 동시 활성 세션 수를 넣는다. 대략 "피크 시간 요청 수 ÷ 3600 × 평균 응답 시간(초)"이다.
3. **SLA**: 업무별 목표를 `sla`에 넣는다. `max`는 이하일 때, `min`은 이상일 때 통과다. 지표 이름은 `p50_ttft_ms`, `p95_ttft_ms`, `mean_ttft_ms`, `p95_itl_ms`, `mean_itl_ms`, `p50_e2e_s`, `p95_e2e_s`, `requests_per_second`, `output_tokens_per_second`, `error_rate` 다.
   - EvalHub 결과(Dashboard·MLflow)의 PASS/FAIL 은 `provider-kbl-perf.json`의 `pass_criteria`(평균값)로 따로 판정한다. 리포트는 p95 로 판정하므로 둘이 다를 수 있다.
4. **고객 프롬프트**: 비식별 처리한 jsonl(`prompts-example.jsonl` 형식)을 S3(rustfs, Secret `kbl-eval-s3`)에 올리고 `scenarios.yaml` 맨 아래 `customer_prompts` 주석을 푼다. job 의 `test_data_ref` 로 pod 의 `/test_data` 에 받아 GuideLLM `data` 로 쓴다.
5. **새 시나리오**: `scenarios.yaml`과 `provider-kbl-perf.json`의 `benchmarks`에 같은 id로 추가한다.

## 결과 해석 예

- **용량 산정**: `capacity`의 요청/초가 R이고 평균 응답 시간(E2E)이 T초이면, 이 서버 1대가 동시에 감당하는 요청은 약 R × T다. 피크 동시 요청이 그보다 많으면 레플리카(GPU)를 늘린다.
- **TTFT가 동시 사용자에 따라 급격히 커진다**: 큐잉이 시작된 것이다. vLLM `--max-num-seqs`와 KV cache 크기(`--gpu-memory-utilization`)를 조정하거나 레플리카를 늘린다.
- **긴 입력에서 TTFT가 크다**(RAG, 요약): prefill 비용 때문이다. 검색 청크 수를 줄이거나 prefix caching을 켜는 것을 검토한다.
- **배포 승인 기준 예**: 품질과 성능을 함께 건다. 예: "골든셋 golden_score ≥ 0.6(`../evaluation`) **그리고** chat_peak TTFT p95 ≤ 1.5초".

## 주의사항

- **측정 경로**: 대상은 고객이 호출하는 경로(Raw 8443 kube-rbac-proxy, MaaS Gateway)다. 아래 2026-11-06 검증 결과는 그 전에 vLLM 직접 포트(8080, `model-hub` 모델)로 잰 값이라 경로 오버헤드가 빠져 있다.
- **GPU 공유**: 이 SNO 는 GPU 1장을 time-slicing 으로 Raw 와 MaaS 가 나눠 쓴다. 한 대상을 잴 때 다른 대상에 트래픽이 없어야 한다(run.py 는 순차 실행).
- **간섭**: 같은 GPU를 쓰는 다른 트래픽(가드레일 self-check, 다른 평가 job)과 겹치면 수치가 나빠진다. 측정 중에는 다른 부하를 멈춘다.
- **WARMUP 단위**: `WARMUP`은 **초 단위**다(기본 5초). adapter 는 `"10%"`를 `10`으로 바꿔 넘기는데, GuideLLM이 이를 10초로 처리한다. 그래서 30초 측정이 실제로는 20초가 된다.
- **throughput 프로파일**: `rate`(최대 동시 요청 수)가 반드시 있어야 한다. 없으면 `ThroughputProfile requires a rate parameter`로 실패한다.
- **측정 시간이 짧을 때**: 30초 측정은 경향 확인용이다. 특히 `synchronous`는 요청 수가 적다(30초에 4~5건). SLA 판정에 쓸 때는 `MAX_SECONDS=120` 이상을 권장한다.
- **sweep 프로파일**: 단계가 여러 개인 프로파일(sweep)은 첫 단계만 EvalHub 지표로 보고된다. 이 예제는 단일 단계 프로파일만 쓴다.
- **tokenizer**: 합성 데이터의 토큰 길이를 맞추는 데 쓰며 기본은 HF Hub에서 받는다(`PROCESSOR`). 폐쇄망에서는 tokenizer를 S3에 올리고 `test_data_ref`와 `PROCESSOR=/test_data/tokenizer`를 쓰는 방식이 필요하다. adapter v0.7.2 에서 이 방식이 동작하는지는 아직 확인하지 않았다(upstream 수정은 2026-11-04).
- GuideLLM provider 이미지(`quay.io/evalhub/community-guidellm`)는 커뮤니티 이미지다. 운영 도입 전에 Red Hat 지원 범위를 확인한다.

## 검증 결과 (2026-11-06, rhoai-35 SNO, GPU 1장, qwen3-4b-instruct-2507, vLLM 직접 포트)

시나리오당 30초 측정. `capacity`는 rate 수정 후 재실행했다. 앞의 4개는 warmup이 10초로 처리되어 실제 측정은 20초였다.

| 시나리오 | 부하 | 토큰(입/출) | 완료/오류 요청 | 요청/초 | 출력 토큰/초 | TTFT 평균(ms) | TTFT 중앙/p95(ms) | ITL 평균(ms) | E2E 중앙/p95(s) | SLA |
|---|---|---|---|---|---|---|---|---|---|---|
| chat_baseline | synchronous | 256/256 | 5/0 | 0.13 | 36.8 | 78 | 58/129 | 27.0 | 6.9/7.0 | PASS (≤500) |
| chat_peak | concurrent ×8 | 256/256 | 17/0 | 0.80 | 175.5 | 545 | 737/745 | 30.5 | 8.3/8.3 | PASS (≤1500) |
| rag_consult | concurrent ×4 | 2048/256 | 9/0 | 0.40 | 73.6 | 2,033 | 2,021/2,731 | 35.0 | 10.9/11.6 | PASS (≤3000) |
| doc_summary | concurrent ×2 | 4096/512 | 3/0 | 0.10 | 42.4 | 2,418 | 2,152/2,960 | 32.9 | 19.2/19.3 | **FAIL** (출력 ≥50 tok/s) |
| capacity | throughput ×16 | 256/256 | 49/0 | 1.92 | 326.0 | 989 | 791/1,406 | 34.0 | 9.7/9.7 | PASS (≥1 rps) |

읽는 법:
- **TTFT는 동시 사용자와 입력 길이에 따라 커진다.** 1명 78ms, 8명 545ms, 긴 입력(2048토큰) 2초, 4096토큰 2.4초다. 긴 입력에서는 prefill이 TTFT를 좌우한다.
- **ITL은 27~35ms로 안정적이다.** 사용자 1명에게 초당 약 30토큰이 흘러나오므로 사람이 읽기에 충분한 속도다.
- **처리량**: 동시 16 상한에서 서버 전체 출력이 초당 약 326토큰(요청 약 1.9건/초)이다. 상담 1건(출력 256토큰)에 약 10초가 걸린다면, 이 GPU 1장은 동시 진행 상담 약 16~19건까지 TTFT 약 1초 수준으로 감당한다.
- **doc_summary FAIL**: 동시 2명에 출력 512토큰이라 서버 전체 출력이 초당 42토큰에 그쳤다. 요약 업무를 동시에 많이 처리하려면 동시 요청 수를 늘리거나 GPU나 레플리카를 추가해야 한다. 또는 SLA를 "사용자당 초당 토큰" 기준으로 다시 정의한다.
- **설정 덮어쓰기**: `RATE_SCALE=0.5 MAX_SECONDS=45 SCENARIOS=chat_peak`로 실행하니 동시 4명, 45초로 반영되었다. 결과는 21건, TTFT 평균 325ms였다.
