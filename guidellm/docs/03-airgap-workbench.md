# 폐쇄망 워크벤치에서 GuideLLM 실행: 반입 목록과 준비

폐쇄망 클러스터의 RHOAI 워크벤치에서 **EvalHub Python SDK** 로 GuideLLM job 을 제출한다. 이때 밖에서 가져와야 할 것과, 클러스터 안에서 준비할 것을 정리한다.
EvalHub 는 이미 설치되어 있다고 가정한다(EvalHub CR, 테넌트, MLflow).
받는 방법과 안에서의 작업 절차(명령)는 [04-airgap-import-guide.md](04-airgap-import-guide.md) 를 본다.

## 근거 표기
- **[문서]** RHOAI Self-Managed 3.5 공식 문서(문서 이름과 절)
- **[소스]** eval-hub, eval-hub-sdk 1.0.7, eval-hub-contrib(GuideLLM adapter v0.7.2), GuideLLM 0.5.3 소스. 3.5 문서에는 없다
- **[실측]** 테스트 클러스터(연결 환경)에서 확인. 폐쇄망에서는 아직 확인하지 않았다

## 1. 실행 흐름

```
워크벤치 (SDK)  ── REST(Bearer: 워크벤치 SA 토큰, X-Tenant) ──▶  EvalHub 서버 (redhat-ods-applications)
                                                                    │ Job 생성 (테넌트 네임스페이스)
                                                                    ▼
                   GuideLLM job pod = init(S3 → /test_data) + adapter(GuideLLM) + sidecar(모델 프록시)
                                                                    │
                       tokenizer: /test_data/<tokenizer>   ────────┤  (HF Hub 대신)
                       모델 호출: sidecar → Raw 8443 / MaaS     ────┤
                       결과: MLflow (클러스터 내부)          ────────┘
```

GuideLLM 은 워크벤치가 아니라 **job pod 안에서** 돈다. 그래서 워크벤치에는 SDK 만 있으면 되고, GuideLLM 이미지와 tokenizer 는 job pod 쪽에 필요하다.

## 2. 반입 목록

| # | 항목 | 필요 | 반입 형태 | 근거 |
|---|---|---|---|---|
| 1 | **워크벤치 이미지** (기본 워크벤치 이미지 + `eval-hub-sdk[client]==1.0.7`) | 필수 | 컨테이너 이미지 (밖에서 빌드해서 반입 권장) 또는 wheel 19개(약 4.4MB) | [문서] *Managing OpenShift AI* 2.1, 2.5 |
| 2 | **GuideLLM adapter 이미지** `quay.io/evalhub/community-guidellm@sha256:c47ac0bc122133ee4709e33c5144da16ee1e1d9e8b9ba6f5470ef16a1e7efeba` (v0.7.2) | 필수 | oc-mirror `additionalImages` (수 GB) | [실측] provider 정의, quay.io 태그 |
| 3 | **tokenizer** | 필수 | 반입하지 않는다. 이미 미러된 **modelcar 이미지에서 꺼낸다** (3.3) | [소스] adapter 의 offline tokenizer 처리 |
| 4 | 코드 (`basic_job.py` 같은 노트북/스크립트) | 필수 | 내부 Git, 또는 워크벤치에 업로드 | - |
| 5 | 고객 프롬프트 파일 (jsonl) | 선택 | 비식별 처리 후 S3/PVC | [문서] *Evaluating AI systems* 2.17, 2.18 |
| - | 벤치마크 데이터셋 | **불필요** | - | [소스] 합성 데이터는 GuideLLM 이 Faker 로 생성 |
| - | EvalHub init/sidecar 이미지 (`registry.redhat.io/rhoai/odh-eval-hub-rhel9@sha256:…`) | 불필요 (RHOAI 미러에 포함) | - | [실측] `evalhub-config` |
| - | 시스템 provider `guidellm` 이미지 (`community-guidellm:v0.5.4`) | 쓰지 않으면 불필요 | 쓰려면 tag 참조라 ImageTagMirrorSet 이 따로 필요 | [실측] |

### 2.1 워크벤치 이미지 (SDK)
**방법 A (권장): 밖에서 빌드하고 이미지만 반입**
```dockerfile
# 베이스: 폐쇄망 클러스터와 같은 RHOAI 버전의 워크벤치 이미지
#   oc get imagestream -n redhat-ods-applications <이미지> -o jsonpath='{.spec.tags[*].from.name}'
FROM registry.redhat.io/rhoai/<워크벤치 이미지>@sha256:...
USER 1001
RUN pip install --no-cache-dir "eval-hub-sdk[client]==1.0.7" pyyaml
```
```bash
podman build -t <내부레지스트리>/kbl/workbench-evalhub:1.0 .
# 반입: oc-mirror additionalImages, 또는 podman save / skopeo copy 로 내부 레지스트리에 넣는다
```
등록: Dashboard → Settings → Workbench images → Import new image ([문서] *Managing OpenShift AI* 2.5 "Import a custom workbench image").

**방법 B: wheel 을 반입해서 안에서 빌드**
```bash
# 밖: 베이스 이미지 안에서 받으면 Python 버전(3.12)과 플랫폼이 맞는다
podman run --rm -v $PWD/wheels:/wheels:Z <워크벤치 베이스 이미지> \
  pip download "eval-hub-sdk[client]==1.0.7" pyyaml -d /wheels
# 안: Containerfile
COPY wheels/ /tmp/wheels/
RUN pip install --no-cache-dir --no-index --find-links /tmp/wheels "eval-hub-sdk[client]==1.0.7" pyyaml \
 && rm -rf /tmp/wheels
```
- 받아지는 패키지(Python 3.12, x86_64 기준): eval_hub_sdk, httpx, httpcore, h11, anyio, pydantic, pydantic_core, pydantic_settings, python_dotenv, annotated_types, typing_extensions, typing_inspection, certifi, idna, urllib3, importlib_metadata, zipp, lazy_importer, pyyaml
- 바이너리 wheel 은 `pydantic_core` 와 `pyyaml` 뿐이다. Python 버전이 다른 베이스(3.11 등)를 쓰면 다시 받아야 한다.
- 고객사 Nexus 에 PyPI proxy 가 있으면 `pip install --index-url <nexus>/simple …` 로 바로 설치할 수 있다.
- SDK 는 서버와 같은 1.0.x 를 쓴다. RHOAI 3.5 서버는 1.0.0 이고, SDK 1.0.0 과 1.0.7 로 동작을 확인했다.

### 2.2 GuideLLM adapter 이미지
```yaml
# imageset-config.yaml (oc-mirror v2)
mirror:
  additionalImages:
    - name: quay.io/evalhub/community-guidellm@sha256:c47ac0bc122133ee4709e33c5144da16ee1e1d9e8b9ba6f5470ef16a1e7efeba
```
- digest 로 참조하므로 oc-mirror 가 만든 ImageDigestMirrorSet 이 적용된다. provider 정의(`provider-kbl-perf.json`, `provider-kbl-guidellm.yaml`)의 image 는 바꾸지 않아도 된다.
- 이 이미지는 **community 이미지**다. 운영 도입 전에 Red Hat 지원 범위를 확인한다.
- 크기가 수 GB 다. 처음 pull 할 때 SNO 노드에 DiskPressure 가 생긴 적이 있으므로 노드 디스크 여유를 확인한다.
- RHOAI 3.5.3 operator 의 relatedImages(oc-mirror 가 operator 와 함께 자동으로 받는 목록)에 이 이미지가 들어 있는지는 아직 확인하지 않았다. 다음 명령으로 확인한다.
  `oc get csv -n redhat-ods-operator -o jsonpath='{.items[?(@.spec.displayName=="Red Hat OpenShift AI")].spec.relatedImages[*].image}' | tr ' ' '\n' | grep -i guidellm`

## 3. 클러스터 안에서 준비할 것 (반입 아님)

### 3.1 provider 등록
- `kbl_perf`: REST 로 테넌트에 등록한다(`register-provider.sh`, 또는 SDK `client.providers.create(...)`). 이미지 참조는 그대로 둔다.
- `kbl_guidellm`: ConfigMap(`evaluation/evalhub/hub/provider-kbl-guidellm.yaml`)과 EvalHub CR `spec.providers` 를 쓴다.

### 3.2 워크벤치 → EvalHub 접속
| 항목 | 설정 | 근거 |
|---|---|---|
| 토큰 | SDK 가 pod 의 SA 토큰(`/var/run/secrets/kubernetes.io/serviceaccount/token`)을 자동으로 쓴다 | [소스] sdk `_resolve_auth_token` |
| 권한 | 워크벤치 SA(보통 워크벤치 이름)에 테넌트의 Role `kbl-evalhub-user` 를 바인딩한다. 이 Role 에는 evaluations/providers/collections 와 `mlflow.kubeflow.org` experiments 권한이 있다(`evaluation/evalhub/tenant/rbac.yaml`) | [실측] EvalHub `auth.yaml` 의 SubjectAccessReview |
| 주소 | 클러스터 내부 `https://evalhub.redhat-ods-applications.svc:8443` 를 권장한다(라우터를 거치지 않음) | [실측] |
| CA | 내부 주소는 service CA 로 서명되어 있다. `ca_bundle_path="/var/run/secrets/kubernetes.io/serviceaccount/service-ca.crt"` 로 지정한다 | [소스] sdk `_resolve_ca_bundle` |
| 네트워크 | EvalHub 가 `redhat-ods-applications` 에 있으면 호출하는 쪽 네임스페이스에 라벨 `opendatahub.io/generated-namespace=true` 가 필요하다. 워크벤치를 테넌트 프로젝트(`kbl-eval`)에 만들면 이미 붙어 있다 | [실측] |

### 3.3 tokenizer: modelcar 에서 꺼내 S3 로
합성 데이터(`prompt_tokens=N`)의 길이를 맞추려면 tokenizer 가 필요하다. 기본값(`Qwen/Qwen3-1.7B`, 또는 지정하지 않을 때 쓰는 `gpt2`)은 HF Hub 에서 받으므로 폐쇄망에서 실패한다.
adapter v0.7.2 는 `processor` 가 **`/test_data/` 아래 디렉터리**면 HF offline 환경변수를 켜고 그 디렉터리를 쓴다([소스] eval-hub-contrib `d69561e`, v0.7.2 에 포함).

```bash
# 미러된 modelcar 에서 tokenizer 레이어만 받는다 (가중치 제외, 약 16MB). 자세한 방법과 대안은 04 문서 4장
python3 extract-tokenizer.py <내부레지스트리>/<modelcar 이미지>@sha256:... ./qwen3-1.7b
# 테넌트의 S3 에 올린다 (예: rustfs, Secret kbl-eval-s3 와 같은 버킷)
aws s3 cp --recursive ./qwen3-1.7b s3://kbl-eval/perf/v1/qwen3-1.7b/ --endpoint-url <S3 endpoint>
```
job 에서는 benchmark 에 다음을 넣는다.
```
test_data_ref : s3 {bucket: kbl-eval, key: perf/v1, secret_ref: kbl-eval-s3}   → pod 의 /test_data
processor     : /test_data/qwen3-1.7b
```
- benchmark 하나에 `test_data_ref` 는 하나뿐이다. 고객 프롬프트도 쓰려면 tokenizer 와 같은 prefix(`perf/v1/`) 아래에 둔다.
- S3 대신 PVC 를 쓸 수도 있다(`test_data_ref.pvc`, RWX 또는 ROX 권장, [문서] 2.18). 워크벤치의 RWO PVC 는 job pod 와 동시에 마운트할 수 없다.

### 3.4 모델 접속 Secret
대상별 Secret 을 테넌트에 만든다(`setup-targets.sh`). 둘 다 클러스터 안에 있는 CA 를 쓰므로 반입할 것은 없다.
- Raw: `ca_cert` = service CA
- MaaS: `api-key` 와 `ca_cert` = 인그레스 CA. 폐쇄망의 인그레스 인증서가 사내 CA 로 서명되어 있다면 그 CA 체인을 넣는다.

## 4. 워크벤치 코드 예 (SDK 1.0.7)

```python
from evalhub.client import SyncEvalHubClient
from evalhub.models import (BenchmarkConfig, ExperimentConfig, JobSubmissionRequest,
                            ModelAuth, ModelConfig, S3TestDataRef, TestDataRef)

client = SyncEvalHubClient(
    base_url="https://evalhub.redhat-ods-applications.svc:8443",
    tenant="kbl-eval",
    ca_bundle_path="/var/run/secrets/kubernetes.io/serviceaccount/service-ca.crt",
)   # 토큰은 워크벤치 SA 토큰을 자동으로 쓴다

job = client.jobs.submit(JobSubmissionRequest(
    name="perf-raw-chat-peak",
    model=ModelConfig(url="https://qwen3-1-7b-predictor.kbl-models.svc.cluster.local:8443/v1",
                      name="qwen3-1-7b", auth=ModelAuth(secret_ref="kbl-perf-raw")),
    experiment=ExperimentConfig(name="kbl-perf-raw"),
    benchmarks=[BenchmarkConfig(
        id="chat_peak", provider_id="<kbl_perf provider id>",
        parameters={
            "profile": "concurrent", "rate": 8, "max_seconds": 120,
            "data": "prompt_tokens=256,output_tokens=256",
            "processor": "/test_data/qwen3-1.7b",      # HF Hub 대신 S3 에서 받은 tokenizer
            "request_type": "text_completions",        # reasoning parser 모델이면 (README 참고)
            "outputs": "json,csv,yaml",                # html 은 템플릿을 인터넷에서 받는다
        },
        test_data_ref=TestDataRef(s3=S3TestDataRef(bucket="kbl-eval", key="perf/v1",
                                                    secret_ref="kbl-eval-s3")),
    )],
))
```
- SDK 1.0.7 로 객체 생성과 메서드 이름(`jobs.submit`, `jobs.wait_for_completion`, `jobs.get_logs`)을 확인했다(서버 호출은 안 함). `basic_job.py` 와 같은 패턴이다.
- 서버 1.0.0 의 `test_data_ref` 는 `s3`, `pvc` 만 받는다. SDK 에는 `git`, `hf` 도 있지만 쓰지 않는다.
- 결과: `client.jobs.get(...)` 의 평균 지표, MLflow(내부)의 `benchmarks.json`(p95), Dashboard Evaluations 화면.

## 5. 폐쇄망에서 인터넷을 찾는 지점 (확인 결과)

| 지점 | 연결 환경 동작 | 폐쇄망 대응 |
|---|---|---|
| tokenizer (`processor`) | HF Hub 다운로드 | `/test_data/<dir>` (3.3) |
| HTML 리포트 | GitHub 에서 템플릿 다운로드 | `outputs: json,csv,yaml`. tokenizer 를 `/test_data` 에서 쓰면 adapter 가 html 을 자동으로 뺀다 |
| 합성 데이터 | 이미지 안의 Faker 로 생성 | 없음 |
| GuideLLM adapter 이미지 | quay.io | 미러 (2.2) |
| MLflow, EvalHub, 모델 | 클러스터 내부 | 없음 |

## 6. 아직 확인하지 않은 것
- 폐쇄망에서의 실제 실행(오프라인 tokenizer 포함). 연결 환경에서도 `/test_data` tokenizer 방식은 아직 돌려 보지 않았다.
- RHOAI 3.5.3 relatedImages 에 GuideLLM 이미지가 들어 있는지 (2.2 의 명령으로 확인).
- 워크벤치 SA 이름과 RoleBinding: 워크벤치 pod 의 `spec.serviceAccountName` 을 보고 바인딩한다.
