# 서빙 성능 평가 예제 (EvalHub + GuideLLM)

KB Life 업무 시나리오별로 **LLM 서빙 성능**(응답 속도, 처리량, 동시 사용자 수용량)을 측정하고 SLA 통과 여부를 표로 보여준다.
RHOAI 3.5 EvalHub에 포함된 **GuideLLM** provider를 사용한다.

> 운영에서 부하 테스트를 설계·수행하는 원칙과 체크리스트는 [BEST-PRACTICES.md](BEST-PRACTICES.md)를 본다.

> 이 예제는 **속도와 용량**만 본다. 답이 맞는지(품질)는 `../evaluation/`(KMMLU, 골든셋)에서 평가한다.
> 같은 모델이라도 GPU, vLLM 설정, 레플리카 수, 양자화 여부에 따라 결과가 달라진다. 그래서 이 수치는 모델 자체가 아니라 **서빙 환경**의 성능이다.

## 구성

```
guidellm/
├── config.env               # 부하 설정 (측정 시간, 부하 배수, 시나리오 선택, 대상 모델)
├── scenarios.yaml           # KB Life 업무 시나리오 (토큰 길이, 부하 방식, SLA 기준)
├── provider-kbl-perf.json   # EvalHub provider 정의 (시스템 GuideLLM 이미지 재사용)
├── register-provider.sh     # provider 를 REST API 로 테넌트에 등록/갱신
├── run.sh / run.py          # 시나리오를 하나씩 차례로 실행
├── report.py                # 결과 → SLA 표 (Markdown / CSV)
└── results/<시각>/          # 요청·결과 JSON, sla-report.md, sla-report.csv
```

- `../evaluation/`이 배포한 EvalHub 서버, 테넌트 `kbl-eval`, 모델 접근 권한을 **함께 사용**한다. 소스와 매니페스트는 공유하지 않는다.
- provider `kbl_perf`는 EvalHub CR(ConfigMap)을 고치지 않도록 **REST API로 테넌트에 등록**한다. `run.sh`가 자동으로 등록하거나 갱신한다.
- 시나리오를 한 job에 몰아 넣으면 EvalHub가 pod를 **동시에** 띄워 서로 부하를 간섭한다. 그래서 `run.py`는 시나리오마다 job을 하나씩 제출하고, 끝나기를 기다렸다가 다음을 실행한다.

## 실행

```bash
./run.sh                                    # 기본: 5개 시나리오, 각 30초 (전체 약 4~5분)
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
- 중앙값/p95와 요청 수는 GuideLLM이 평가 pod 로그에 출력하는 요약 표에서 `report.py`가 읽는다. GuideLLM adapter v0.5.0은 상세 결과(`benchmarks.json`)를 MLflow로 올리지 않는다. 평가 pod가 정리된 뒤에는 이 칸이 `-`로 표시된다.

## 시나리오 (`scenarios.yaml`)

| 시나리오 | 업무 | 입력/출력 토큰 | 부하 | SLA 예시 |
|---|---|---|---|---|
| `chat_baseline` | 상담 챗봇, 1명 기준선 | 256 / 256 | synchronous (1명씩 순차) | TTFT 평균 ≤ 500ms |
| `chat_peak` | 상담 챗봇, 동시 사용자 | 256 / 256 | concurrent ×8 | TTFT 평균 ≤ 1500ms |
| `rag_consult` | RAG 상담 (약관 청크 포함 긴 입력) | 2048 / 256 | concurrent ×4 | TTFT 평균 ≤ 3000ms |
| `doc_summary` | 약관·청약서 요약 | 4096 / 512 | concurrent ×2 | 출력 ≥ 초당 50토큰 |
| `capacity` | 최대 처리량 | 256 / 256 | throughput (동시 16 상한, 포화까지) | 요청 ≥ 초당 1건 |

### KB Life 업무에 맞추기
1. **토큰 길이**: 실제 서비스 로그(또는 vLLM `/metrics`의 `prompt_tokens`, `generation_tokens` 히스토그램)에서 업무별 평균 입력·출력 길이를 구해 `prompt_tokens`와 `output_tokens`에 넣는다. RAG는 "시스템 프롬프트 + 검색 청크 수 × 청크 길이 + 질문"으로 계산한다.
2. **동시 사용자(rate)**: 피크 시간대의 동시 활성 세션 수를 넣는다. 대략 "피크 시간 요청 수 ÷ 3600 × 평균 응답 시간(초)"이다.
3. **SLA**: 업무별 목표를 `sla`에 넣는다. `max`는 이하일 때, `min`은 이상일 때 통과다. 지표 이름은 `mean_ttft_ms`, `mean_itl_ms`, `requests_per_second`, `output_tokens_per_second`, `prompt_tokens_per_second`다.
   - EvalHub 결과의 PASS/FAIL은 `provider-kbl-perf.json`의 `pass_criteria`로 판정한다. 두 곳의 기준을 맞춰 둔다.
4. **새 시나리오**: `scenarios.yaml`과 `provider-kbl-perf.json`의 `benchmarks`에 같은 id로 추가한다.

## 결과 해석 예

- **용량 산정**: `capacity`의 요청/초가 R이고 평균 응답 시간(E2E)이 T초이면, 이 서버 1대가 동시에 감당하는 요청은 약 R × T다. 피크 동시 요청이 그보다 많으면 레플리카(GPU)를 늘린다.
- **TTFT가 동시 사용자에 따라 급격히 커진다**: 큐잉이 시작된 것이다. vLLM `--max-num-seqs`와 KV cache 크기(`--gpu-memory-utilization`)를 조정하거나 레플리카를 늘린다.
- **긴 입력에서 TTFT가 크다**(RAG, 요약): prefill 비용 때문이다. 검색 청크 수를 줄이거나 prefix caching을 켜는 것을 검토한다.
- **배포 승인 기준 예**: 품질과 성능을 함께 건다. 예: "골든셋 golden_score ≥ 0.6(`../evaluation`) **그리고** chat_peak TTFT p95 ≤ 1.5초".

## 주의사항

- **측정 경로**: EvalHub job sidecar의 모델 프록시는 OpenShift service CA를 신뢰하지 않는다. 그래서 KServe 인증 포트(8443) 대신 vLLM 직접 포트(`<isvc>-metrics:8080`)를 호출한다. kube-rbac-proxy와 라우터를 거치지 않으므로 실제 서비스 경로보다 **약간 낙관적인 수치**다.
- **간섭**: 같은 GPU를 쓰는 다른 트래픽(가드레일 self-check, 다른 평가 job)과 겹치면 수치가 나빠진다. 측정 중에는 다른 부하를 멈춘다.
- **WARMUP 단위**: `WARMUP`은 **초 단위**다(기본 5초). adapter v0.5.0은 `"10%"`를 `10`으로 바꿔 넘기는데, GuideLLM이 이를 10초로 처리한다. 그래서 30초 측정이 실제로는 20초가 된다.
- **throughput 프로파일**: `rate`(최대 동시 요청 수)가 반드시 있어야 한다. 없으면 `ThroughputProfile requires a rate parameter`로 실패한다.
- **측정 시간이 짧을 때**: 30초 측정은 경향 확인용이다. 특히 `synchronous`는 요청 수가 적다(30초에 4~5건). SLA 판정에 쓸 때는 `MAX_SECONDS=120` 이상을 권장한다.
- **sweep 프로파일**: 단계가 여러 개인 프로파일(sweep)은 첫 단계만 EvalHub 지표로 보고된다. 이 예제는 단일 단계 프로파일만 쓴다.
- **tokenizer**: 합성 데이터의 토큰 길이를 맞추는 데 쓰며 기본은 HF Hub에서 받는다(`PROCESSOR`). 폐쇄망에서는 tokenizer를 S3에 올리고 `test_data_ref`와 `PROCESSOR=/test_data/tokenizer`를 쓰는 방식이 필요하다. 다만 v0.5.0 adapter가 이를 지원하는지는 확인되지 않았다(upstream 수정은 2026-11-04).
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
