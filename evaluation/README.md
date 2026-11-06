# EvalHub 모델 평가 예제 (RHOAI 3.5)

RHOAI 3.5의 **EvalHub**(GA)로 서빙 중인 모델(`model-hub/qwen3-4b-instruct-2507-v10`)을 평가하는 예제 2가지다.
3.5에서 LMEvalJob은 deprecated되었고 EvalHub가 대체한다.

| 예제 | 데이터 | 채점 | provider |
|---|---|---|---|
| ① 공개 한국어 평가셋 | Hugging Face **KMMLU** 4과목(회계·경제·세무·법률) | 4지선다 정답 일치(exact_match) | `kbl_lm_eval_ko` (시스템 lm-eval 이미지 재사용) |
| ② KB Life 골든셋 | 업무 담당자가 만든 보험 Q&A(CSV) | 정답 일치, 핵심용어, 금지표현, LLM-as-judge | `kbl_golden` (커스텀 adapter) |

배포는 Argo CD AppProject **`kbl-evaluation`**으로 하며, 클러스터 GitOps 및 가드레일 프로젝트와 분리되어 있다. 평가 실행은 REST API로 한다(`jobs/submit.sh`).

## 아키텍처

```
                     ┌──────────── redhat-ods-applications ────────────┐
 submit.sh ──HTTPS──▶│ EvalHub 서버 (EvalHub CR)        MLflow          │
 (X-Tenant: kbl-eval)│  ├ provider: lm_evaluation_harness (시스템)      │
                     │  ├ provider: kbl_lm_eval_ko  ★ ConfigMap         │
                     │  └ provider: kbl_golden      ★ ConfigMap         │
                     └───────────────┬─────────────────────▲───────────┘
                         Job 생성     │                     │ 결과/지표
                     ┌──────────── kbl-eval (테넌트) ───────┴───────────┐
                     │ Job pod (벤치마크마다 1개)                         │
                     │  ├ init    : S3 test_data_ref → /test_data        │
                     │  ├ adapter : lm-eval 또는 kbl-golden              │
                     │  └ sidecar : EvalHub/MLflow/모델 호출 프록시      │
                     │ PostgreSQL (EvalHub DB)                           │
                     └───────┬────────────────────────────┬─────────────┘
                             │ S3                          │ OpenAI API
                     rustfs (kbl-eval 버킷)        model-hub / qwen3-4b (vLLM)
```

## 디렉터리

```
evaluation/
├── gitops/                 # Argo CD 부트스트랩 (AppProject kbl-evaluation, Application)
├── evalhub/                # Argo CD 동기화 대상
│   ├── tenant/             # kbl-eval 네임스페이스(테넌트 라벨), RBAC, 모델 접근 권한, PostgreSQL
│   ├── secrets/            # SealedSecret (S3, DB). scripts/seal-secrets.sh 로 생성
│   └── hub/                # EvalHub CR, 커스텀 provider ConfigMap 2개
├── datasets/
│   ├── common/             # S3 업로드 도구 (s3_sync.py, s3-env.sh)
│   ├── kmmlu/              # HF → 오프라인 레이아웃 → S3 (stage-to-s3.sh)
│   └── golden/             # 골든셋 CSV, 템플릿, 검증/변환기, 업로드
├── adapters/kbl-golden/    # 골든셋 채점 adapter (eval-hub-sdk FrameworkAdapter) + 단위 테스트
├── jobs/                   # 평가 요청 JSON, 컬렉션 정의, submit.sh / create-collection.sh
└── scripts/seal-secrets.sh
```

## 1. 부트스트랩

```bash
# (1) Secret 봉인 (클러스터별 1회. 결과 파일은 Git 에 커밋해도 안전)
./evaluation/scripts/seal-secrets.sh

# (2) Argo CD 등록 (1회) — 이후 변경은 git push 만
oc apply -f evaluation/gitops/appproject.yaml -f evaluation/gitops/application-evalhub.yaml

# (3) 확인
oc get application kbl-evaluation-evalhub -n openshift-gitops     # Synced / Healthy
oc get evalhub evalhub -n redhat-ods-applications                   # Ready
```

자동으로 처리되는 것:
- `kbl-eval` 네임스페이스에는 `evalhub.trustyai.opendatahub.io/tenant` 라벨이 붙어 있다. operator가 이 라벨을 보고 job ServiceAccount(`evalhub-redhat-ods-applications-job`), RBAC, service CA ConfigMap을 만든다.
- EvalHub를 `redhat-ods-applications`에 두는 경우 `opendatahub.io/generated-namespace=true` 라벨도 필요하다. 이 라벨이 없으면 sidecar가 해당 네임스페이스의 NetworkPolicy에 막힌다.

## 2. 예제 ① — Hugging Face 한국어 평가셋 (KMMLU)

**왜 커스텀 provider인가.** 시스템 lm-eval provider(188개 벤치마크)에는 한국어 벤치마크가 없다. 그러나 같은 adapter 이미지 안의 lm-eval 0.4.8에는 `kmmlu`, `kobest`, `haerae` task가 들어 있다. adapter는 `benchmark_id`를 lm-eval task 이름으로 그대로 넘긴다. 따라서 **이미지를 새로 만들 필요 없이 ConfigMap에 벤치마크 id만 등록하면 된다**(`evalhub/hub/provider-lm-eval-ko.yaml`).

`kmmlu_direct_*`는 생성형 4지선다 task다. 모델이 답한 기호를 정답과 비교(exact_match)하므로 logprobs 없이 vLLM `/v1/completions`로 동작한다.

**데이터 적재 (폐쇄망 대비).** 데이터는 HF Hub에서 직접 받지 않고 S3에 미리 올린다.
```bash
source evaluation/datasets/common/s3-env.sh        # rustfs 접속정보 (다른 S3 는 AWS_* 직접 지정)
./evaluation/datasets/kmmlu/stage-to-s3.sh         # → s3://kbl-eval/datasets/kmmlu-kbl/
# 폐쇄망: 인터넷 PC 에서 STAGE_ONLY=1 로 받고 반입 → UPLOAD_ONLY=1 STAGE_DIR=... 로 업로드
```

lm-eval adapter는 `/test_data`가 아래 구조이면 **자동으로 HF 오프라인 모드**(`HF_HOME=/test_data`)로 전환한다.
```
/test_data/tokenizer/                     ← job parameters.tokenizer=/test_data/tokenizer
/test_data/datasets/, /test_data/hub/     ← HF cache (lm-eval 이 여기서 KMMLU 를 읽음)
/test_data/kmmlu-<과목>/dataset_dict.json ← tokenizer 옆 DatasetDict 폴더 = 오프라인 판단 근거
```
HF cache 형식은 `datasets` 버전에 묶여 있다. 그래서 `stage-to-s3.sh`는 adapter 이미지와 같은 `datasets==5.0.1`로 cache를 만든다.

**실행**
```bash
./evaluation/jobs/submit.sh 01-kmmlu.json
```
`01-kmmlu.json`의 주요 파라미터는 다음과 같다.
- `tokenizer`: 서빙 이름이 HF id가 아니므로 tokenizer 경로를 지정한다.
- `num_examples`: 과목당 문항 수를 제한해 빠르게 돌린다. 전체 평가는 이 값을 빼면 된다.
- `test_data_ref.s3`: 위에서 올린 S3 데이터 위치다.

## 3. 예제 ② — KB Life 골든셋

### 3-1. 골든셋 만들기 (업무 담당자)

1. `datasets/golden/template.csv`를 복사해 엑셀에서 작성한다. 컬럼은 아래와 같다.

   | 컬럼 | 설명 |
   |---|---|
   | `id` | 고유 ID (예: KBL-0031) |
   | `category` | 약관 / 상품 / 청구 / 세제 / 계약관리 (지표 키는 `scoring.py`의 `CATEGORY_KEYS`) |
   | `type` | `open`(주관식) 또는 `mcq`(객관식) |
   | `question` | 실제 고객 질문 형태로 작성 |
   | `choices`, `answer` | mcq 전용. `A. 보기1\|B. 보기2\|...`, 정답 기호 |
   | `reference_answer` | open 필수. 근거 조항에 맞는 모범답안 |
   | `required_keywords` | 답변에 꼭 들어가야 하는 핵심 용어 (`;` 구분). 예: `15일;30일` |
   | `forbidden` | 들어가면 0점 처리할 잘못된 안내 (`;` 구분). **단정형 문장 전체**로 쓴다. 예: `원금이 보장됩니다` |
   | `source`, `owner`, `reviewed_at` | 근거(약관/법령), 작성부서, 검토일. 감사 추적용 |

2. 변환과 검증을 한다. 필수값 누락, 중복 id, 잘못된 정답 기호, 날짜 형식을 검사한다.
   ```bash
   python3 evaluation/datasets/golden/csv-to-jsonl.py my-golden.csv -o /tmp/out.jsonl
   ```

3. 검토 후 버전별로 업로드한다. 버전은 S3 경로로 구분하며, 평가 결과에 `dataset_version`과 파일 해시가 함께 기록된다.
   ```bash
   ./evaluation/datasets/golden/upload.sh my-golden.csv v2    # → s3://kbl-eval/golden/v2/
   ```

작성 팁:
- **실제 고객정보는 넣지 않는다.** 개인정보가 필요한 시나리오는 가상 데이터로 만든다.
- 한 분류에 최소 20~30문항 이상을 두어야 점수 변동이 안정적이다.
- `forbidden`에 부분 문구(예: `원금 보장`)를 쓰면 "원금 보장**이 아닌**" 같은 부정문도 걸린다. 실제로 확인된 오탐이다.
- 법령이나 약관이 바뀌면 해당 문항을 수정하고 버전을 올린다. 예: 예금자보호 한도는 2025.9부터 1억원이다.

### 3-2. 채점 방식 (`adapters/kbl-golden/scoring.py`)

| 지표 | 계산 |
|---|---|
| `golden_score` (primary) | 문항 점수 평균 |
| 문항 점수 (mcq) | 정답 1 / 오답 0 |
| 문항 점수 (open) | 0.6 × judge + 0.4 × keyword_recall. **금지표현이 나오면 0점** |
| `mcq_accuracy` | 객관식 정답률 |
| `keyword_recall` | 핵심용어 포함률 (공백 무시) |
| `forbidden_rate` | 금지표현이 나온 주관식 비율 (낮을수록 좋음) |
| `judge_score` | LLM-as-judge 1~5점을 0~1로 환산. 숫자(기간·금액·비율)가 틀리면 2점 이하로 채점하도록 프롬프트에 규칙을 넣었다 |
| `category_*` | 분류별 golden_score |

job parameters로 동작을 바꿀 수 있다. 바꿀 수 있는 항목은 `system_prompt`, `judge`(true/false), `judge_url`, `judge_model`, `concurrency`, `dataset_version`이다.
- 판정 모델은 기본으로 평가 대상과 같은 모델을 쓴다. 이 경우 자기 평가 편향이 생기므로, 운영에서는 더 큰 모델이나 다른 모델을 `judge_url`과 `judge_model`로 지정한다.
- 문항별 응답과 점수(`golden/item_results.jsonl`), 요약(`golden/summary.json`)은 MLflow artifact로 저장된다.

### 3-3. adapter 이미지 빌드

```bash
cd evaluation/adapters/kbl-golden
uv run --with pytest pytest -q test_scoring.py         # 채점 로직 단위 테스트
IMAGE=quay.io/<org>/kbl-golden-adapter TAG=v1 ./build.sh --push
```
이미지 주소는 `evalhub/hub/provider-kbl-golden.yaml`의 `runtime.k8s.image`와 맞춘다. 폐쇄망에서는 사내 레지스트리 주소를 쓴다.

### 3-4. 실행

```bash
./evaluation/jobs/submit.sh 02-golden.json
```

## 4. 배포 승인 컬렉션 (①+② 한 번에)

`jobs/collection-kbl-release-v1.json`은 골든셋(weight 3)과 KMMLU 4과목(각 weight 1)을 묶은 것이다. 벤치마크별 통과 기준과 전체 가중 평균 기준을 함께 둔다.
```bash
./evaluation/jobs/submit.sh 03-release-collection.json   # 컬렉션 등록(없으면) → 평가 → PASS/FAIL
```
이 컬렉션은 S3 위치(`test_data_ref`)를 포함한다. 그래서 ConfigMap(system collection)이 아니라 **REST API로 테넌트에 등록**한다. ConfigMap 로더는 `test_data_ref.s3.secret_ref`를 인식하지 못해 EvalHub 서버가 기동에 실패한다(RHOAI 3.5에서 확인).

## 5. 결과 보기

- `submit.sh` 출력: 벤치마크별 지표, PASS/FAIL, MLflow experiment URL
- API: `GET /api/v1/evaluations/jobs/<id>` (`jobs/last-result.json`에 저장됨)
- MLflow: RHOAI 대시보드 → MLflow. workspace는 `kbl-eval`, experiment는 `kbl-qwen3-4b-*`
- 대시보드: Develop & train → Evaluations

## 6. RHOAI 3.5에서 확인한 제약과 해결

| 증상 | 원인 | 해결 |
|---|---|---|
| job 제출 시 `Workspace context is required` | RHOAI MLflow는 `--static-prefix=/mlflow`로 기동한다. tracking URI에 `/mlflow`가 없으면 workspace 감지가 실패한다 | `MLFLOW_TRACKING_URI=https://mlflow.redhat-ods-applications.svc:8443/mlflow` |
| 컬렉션 ConfigMap을 넣으면 EvalHub 서버 CrashLoop | system collection 로더가 `test_data_ref.s3.secret_ref`를 인식하지 못함 | 컬렉션을 REST API로 등록 |
| 모델 호출 502 `x509: certificate signed by unknown authority` | job sidecar의 모델 프록시는 시스템 CA만 신뢰한다. KServe predictor(8443, kube-rbac-proxy)는 OpenShift service CA 인증서를 쓰는데, 모델용 CA를 지정하는 설정이 없다 | 아래 참고 |

**모델 엔드포인트 관련 주의.** 위 제약 때문에 이 예제는 KServe가 만든 `<isvc>-metrics` Service(vLLM 8080, **인증 없음**)로 모델을 호출한다. 이 포트는 원래부터 클러스터 내부에 열려 있지만, 운영에서는 다음 중 하나를 권장한다.
- 공인 또는 사내 CA 인증서를 쓰는 엔드포인트를 사용한다. 예: Models-as-a-Service gateway나 사내 CA로 서명된 Route. 사내 CA가 시스템 trust에 포함되어 있어야 한다.
- 평가 전용 네임스페이스에서만 8080에 접근하도록 NetworkPolicy를 둔다.
- 이후 버전에서 sidecar가 모델용 CA 설정을 지원하면 kube-rbac-proxy 경로로 되돌린다.

참고로 `model.auth.secret_ref`(api-key) 방식은 MaaS처럼 API 키를 쓰는 엔드포인트에 사용한다.
