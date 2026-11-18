# 폐쇄망 반입 실무 가이드: 워크벤치에서 EvalHub SDK 로 GuideLLM 실행

[03-airgap-workbench.md](03-airgap-workbench.md) 는 무엇이 필요한지를 정리한 문서다. 이 문서는 **밖에서 무엇을 어떻게 받아 가고, 안에서 무엇을 하는지** 를 다루는 절차서다.
명령과 수치는 2026-11-18 에 연결 환경에서 확인한 것이다. 폐쇄망에서 직접 실행해 본 것은 아니다(6장).

## 0. 반입 체크리스트

| 구분 | 항목 | 크기 | 장 |
|---|---|---|---|
| **밖에서 받아 갈 것** | GuideLLM adapter 이미지 `quay.io/evalhub/community-guidellm@sha256:c47ac0bc…` (v0.7.2) | 압축 3.67GB (amd64) | 2 |
| | 워크벤치 이미지: (A) 밖에서 빌드한 이미지, 또는 (B) SDK wheel 묶음 | (B) 약 4.4MB | 1 |
| | 이 저장소의 `guidellm/` 폴더 (스크립트와 문서) | 수백 KB | - |
| **안에서 만들 것** | tokenizer (미러된 modelcar 에서 추출) | 약 16MB (Qwen3-1.7B) | 4 |
| | 고객 프롬프트 jsonl (내부 데이터로 생성) | - | 3 |
| **받지 않아도 되는 것** | 워크벤치 베이스 이미지, EvalHub init/sidecar 이미지 | - | RHOAI 미러에 포함 |
| | modelcar 이미지 | - | 모델 반입 때 이미 미러됨 |
| | 벤치마크 데이터셋, 합성 데이터 생성기 | - | GuideLLM 이미지 안에 있음 (5장) |

---

## 1. 워크벤치 이미지 (EvalHub SDK)

### 1.1 베이스 이미지 고르기
RHOAI 의 기본 워크벤치 이미지는 `redhat-ods-applications` 의 ImageStream 으로 들어 있다([문서] *Managing OpenShift AI* 2.1).
**Jupyter | Data Science | CPU | Python 3.12, 태그 3.5 (recommended)** 를 권장한다. pandas 가 들어 있어 3장의 프롬프트 정리에도 쓸 수 있다.

```bash
# 폐쇄망 클러스터와 같은 RHOAI 버전에서 확인한다 (권장 태그 = workbench-image-recommended=true)
oc get imagestream s2i-generic-data-science-notebook -n redhat-ods-applications \
  -o jsonpath='{range .spec.tags[*]}{.name}{"  "}{.annotations.opendatahub\.io/workbench-image-recommended}{"  "}{.from.name}{"\n"}{end}'
```
테스트 클러스터(RHOAI 3.5.3)의 값은 다음과 같다.
`registry.redhat.io/rhoai/odh-workbench-jupyter-datascience-cpu-py312-rhel9@sha256:b8d61ee1fa27f40677e32160cf34c34bea96a7ce981c32cea80efd387ab831e4`

### 1.2 requirements.txt
```text
eval-hub-sdk[client]==1.0.7
pyyaml
```
- SDK 는 EvalHub 서버와 같은 1.0.x 를 쓴다. RHOAI 3.5 서버는 1.0.0 이고, SDK 1.0.0 과 1.0.7 로 동작을 확인했다.

### 1.3 방법 A (권장): 밖에서 빌드하고 이미지만 반입
인터넷이 되는 빌드 머신에서 진행한다. `registry.redhat.io` 에 로그인해야 한다(`podman login registry.redhat.io`).
```dockerfile
# Containerfile
FROM registry.redhat.io/rhoai/odh-workbench-jupyter-datascience-cpu-py312-rhel9@sha256:b8d61ee1fa27f40677e32160cf34c34bea96a7ce981c32cea80efd387ab831e4
USER 1001
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt
```
```bash
podman build --platform linux/amd64 -t workbench-evalhub:3.5-sdk1.0.7 .
podman run --rm workbench-evalhub:3.5-sdk1.0.7 python -c "import evalhub, yaml; from evalhub.client import SyncEvalHubClient; print('ok')"
# 반입용 파일로 저장 (2.2 의 skopeo 방식과 같다)
skopeo copy containers-storage:localhost/workbench-evalhub:3.5-sdk1.0.7 oci-archive:workbench-evalhub.tar
```
- 베이스 이미지는 반입 대상이 아니다(폐쇄망에 이미 있다). 하지만 빌드한 이미지는 베이스 레이어를 포함하므로 tar 크기는 베이스 이미지와 비슷하다(수 GB).
- 빌드 머신이 arm64(Apple Silicon)면 `--platform linux/amd64` 를 꼭 준다.

### 1.4 방법 B: wheel 을 반입해서 안에서 빌드
반입 용량을 줄이고 싶을 때 쓴다(약 4.4MB). 폐쇄망 안에 빌드 환경(podman 또는 OpenShift BuildConfig)이 있어야 한다.
```bash
# (밖) 베이스 이미지 안에서 받으면 Python 버전(3.12)과 플랫폼(x86_64)이 자동으로 맞는다
mkdir wheels
podman run --rm --platform linux/amd64 -v $PWD:/work:Z -w /work \
  registry.redhat.io/rhoai/odh-workbench-jupyter-datascience-cpu-py312-rhel9@sha256:b8d61ee1… \
  pip download -r requirements.txt -d wheels
tar czf evalhub-sdk-wheels.tgz requirements.txt wheels/

# (밖, 컨테이너 없이) 플랫폼을 지정해 받는 방법
pip download -r requirements.txt -d wheels --only-binary=:all: \
  --platform manylinux2014_x86_64 --platform manylinux_2_17_x86_64 --platform manylinux_2_28_x86_64 \
  --python-version 3.12 --implementation cp
```
- 받아지는 wheel 19개(2026-11-17 기준): eval_hub_sdk, httpx, httpcore, h11, anyio, pydantic, pydantic_core, pydantic_settings, python_dotenv, annotated_types, typing_extensions, typing_inspection, certifi, idna, urllib3, importlib_metadata, zipp, lazy_importer, pyyaml
- 바이너리 wheel 은 `pydantic_core` 와 `pyyaml` 뿐이다. 베이스의 Python 버전이 3.12 가 아니면 다시 받아야 한다.

```dockerfile
# (안) Containerfile: FROM 은 내부 레지스트리에 미러된 같은 베이스 이미지
FROM <내부레지스트리>/rhoai/odh-workbench-jupyter-datascience-cpu-py312-rhel9@sha256:b8d61ee1…
USER 1001
COPY requirements.txt /tmp/requirements.txt
COPY wheels/ /tmp/wheels/
RUN pip install --no-cache-dir --no-index --find-links /tmp/wheels -r /tmp/requirements.txt
```
- 고객사 Nexus 에 PyPI proxy 가 있으면 wheel 없이 `RUN pip install --index-url https://<nexus>/repository/pypi/simple -r /tmp/requirements.txt` 로 끝난다.
- 워크벤치 안에서 바로 쓰려면(이미지 빌드 없이) wheel 을 워크벤치에 올리고 `pip install --no-index --find-links ./wheels -r requirements.txt` 를 실행한다. 다만 워크벤치를 다시 만들면 사라진다.

### 1.5 RHOAI 에 등록
Dashboard → **Settings → Workbench images → Import new image** 에서 이미지 주소(digest 권장)를 넣는다([문서] *Managing OpenShift AI* 2.5 "Import a custom workbench image").
워크벤치는 테넌트 프로젝트(`kbl-eval`)에 만든다. 이 프로젝트에는 EvalHub 접속에 필요한 네트워크 라벨이 이미 있다([03](03-airgap-workbench.md) 3.2).

---

## 2. 받아 갈 이미지

| 이미지 | 필요 | 크기(amd64 압축) | 비고 |
|---|---|---|---|
| `quay.io/evalhub/community-guidellm@sha256:c47ac0bc122133ee4709e33c5144da16ee1e1d9e8b9ba6f5470ef16a1e7efeba` | **필수** | 3.67GB | adapter v0.7.2 (2026-11-04), GuideLLM 0.5.3. `kbl_perf`, `kbl_guidellm` provider 가 쓴다. **RHOAI 3.5.3 relatedImages(149개)에 없음**(2026-11-18 확인) → oc-mirror 가 operator 와 함께 받지 않는다 |
| 1.3 에서 빌드한 워크벤치 이미지 | 방법 A 일 때 | 수 GB | |
| `quay.io/evalhub/community-guidellm:v0.5.4` | 선택 | - | 시스템 provider `guidellm` 용. tag 참조라 ImageTagMirrorSet 이 필요하다. 쓰지 않으면 받지 않는다 |

- 이미지 index 는 amd64 와 attestation 두 항목으로 되어 있다. 클러스터가 x86_64 면 amd64 만 있으면 된다.
- GuideLLM 이미지는 Red Hat 이미지가 아니라 **community 이미지**다. 운영 도입 전에 지원 범위를 확인한다.

### 2.1 oc-mirror (권장, 기존 미러 절차에 넣기)
```yaml
# imageset-config.yaml 의 mirror 아래에 추가
  additionalImages:
    - name: quay.io/evalhub/community-guidellm@sha256:c47ac0bc122133ee4709e33c5144da16ee1e1d9e8b9ba6f5470ef16a1e7efeba
```
```bash
oc mirror -c imageset-config.yaml file://<mirror_dir> --v2          # 밖
oc mirror -c imageset-config.yaml --from file://<mirror_dir> docker://<내부레지스트리> --v2   # 안
```
oc-mirror 가 만든 ImageDigestMirrorSet 을 적용하면 provider 정의의 `quay.io/...@sha256:…` 를 그대로 쓸 수 있다.

### 2.2 skopeo (oc-mirror 를 쓰지 않을 때)
```bash
# 밖: digest 를 유지한 채 파일로 저장
skopeo copy --preserve-digests --override-arch amd64 \
  docker://quay.io/evalhub/community-guidellm@sha256:c47ac0bc122133ee4709e33c5144da16ee1e1d9e8b9ba6f5470ef16a1e7efeba \
  oci-archive:community-guidellm-v0.7.2.tar
sha256sum community-guidellm-v0.7.2.tar > community-guidellm-v0.7.2.tar.sha256

# 안: 내부 레지스트리에 올린다
skopeo copy --preserve-digests oci-archive:community-guidellm-v0.7.2.tar \
  docker://<내부레지스트리>/evalhub/community-guidellm:v0.7.2
skopeo inspect docker://<내부레지스트리>/evalhub/community-guidellm:v0.7.2 | grep -i digest
```
`--override-arch amd64` 로 받으면 index 대신 amd64 manifest 하나만 저장된다. 이 경우 내부 레지스트리의 digest 는 amd64 manifest digest(`sha256:d1864375…`)가 되고 index digest(`c47ac0bc…`)와 다르다. 두 가지 중 하나를 고른다.
- provider 의 image 를 내부 주소와 그 digest 로 바꾼다(`provider-kbl-perf.json` 의 `runtime.k8s.image`).
- 또는 `--override-arch` 없이 `--all` 로 index 전체를 복사해 `c47ac0bc…` 를 유지한다. 그다음 ImageDigestMirrorSet 을 적용한다.
  ```yaml
  apiVersion: config.openshift.io/v1
  kind: ImageDigestMirrorSet
  metadata: {name: evalhub-community}
  spec:
    imageDigestMirrors:
      - source: quay.io/evalhub/community-guidellm
        mirrors: [<내부레지스트리>/evalhub/community-guidellm]
  ```

### 2.3 반입 후 확인
```bash
oc get csv -n redhat-ods-operator -o jsonpath='{range .items[*]}{.spec.relatedImages[*].image}{end}' | tr ' ' '\n' | grep -i guidellm   # 비어 있으면 별도 반입이 맞다
# 노드 디스크 여유: 이미지가 크다. 테스트 SNO 는 처음 pull 할 때 DiskPressure 가 생겼다
oc adm top node
```

---

## 3. 고객 프롬프트 (안에서 만들기)
맞다. 고객 데이터는 반출할 수 없으므로 **안에서 만든다**. 추가로 반입할 도구는 없다.

| 단계 | 도구 | 위치 |
|---|---|---|
| 원본 추출 (상담 로그, FAQ, 콜센터 질문) | 고객사 내부 시스템 | - |
| 정리, 비식별 마스킹, 중복 제거 → jsonl | `prepare-prompts.py` (표준 라이브러리만 사용) 또는 pandas | 이 저장소 / 워크벤치 기본 이미지 |
| (선택) 목표 토큰 길이로 맞추기 | `guidellm preprocess dataset` | **GuideLLM adapter 이미지 안**에 있다 (2장에서 반입) |
| 토큰 길이 분포 확인 | `prepare-prompts.py --tokenizer` (transformers 필요) | Data Science 워크벤치 |
| 업로드 | S3 (`kbl-eval-s3`) 또는 PVC | - |

### 3.1 jsonl 만들기
```bash
python3 prepare-prompts.py 상담질문.csv prompts.jsonl --column question --tokenizer ./qwen3-1.7b
# 입력 6건 → 출력 4건 / 마스킹: 주민등록번호 1, 이메일 1, 휴대폰·전화 2, 계좌번호 형태 1
# 토큰 길이: 중앙값 16, p90 22, 최대 22
```
- 형식은 한 줄에 `{"prompt": "..."}` 하나다([prompts-example.jsonl](../prompts-example.jsonl)). GuideLLM 은 `prompt` 열을 입력으로 쓴다.
- 정규식 마스킹이 잡는 것: 주민등록번호, 이메일, 카드번호, 전화번호, 계좌번호 형태, 10자리 이상 숫자. **이름과 주소는 잡지 못한다.** 고객사 비식별 절차(검수)를 거친다.
  Presidio 같은 NER 도구는 한국어 이름 오탐이 많아서(가드레일 검증 때 확인) 권장하지 않는다.
- 토큰 길이 분포를 보고 `scenarios.yaml` 의 시나리오와 모델의 `max_model_len` 을 맞춘다. 입력과 출력을 합친 값이 `max_model_len` 을 넘으면 요청이 실패한다.

### 3.2 (선택) 목표 길이로 맞추기: `guidellm preprocess dataset`
실제 질문을 쓰되 길이를 시나리오(예: RAG 2048 토큰)에 맞추고 싶을 때 쓴다. GuideLLM 0.5.3 의 명령이고, adapter 이미지에 들어 있다.
```bash
# 안: adapter 이미지로 일회성 실행 (podman 이 있는 내부 머신)
podman run --rm -v $PWD:/work:Z -w /work --entrypoint guidellm \
  <내부레지스트리>/evalhub/community-guidellm@sha256:c47ac0bc… \
  preprocess dataset prompts.jsonl prompts-2048.jsonl \
  --processor /work/qwen3-1.7b \
  --config 'prompt_tokens=2048,output_tokens=256' \
  --short-prompt-strategy concatenate
```
- `--short-prompt-strategy`: 목표보다 짧은 프롬프트 처리 방식(`ignore`, `concatenate`, `pad`, `error`). 짧은 상담 질문을 RAG 길이로 만들려면 `concatenate` 를 쓴다.
- `--processor` 는 4장에서 꺼낸 tokenizer 디렉터리다. HF Hub 이름을 주면 인터넷이 필요하다.
- podman 이 없으면 같은 이미지로 테넌트에 Pod 를 띄워 실행한다(Pod 생성 권한 필요).
- x86_64 머신에서 실행한다. Apple Silicon(arm64)에서 에뮬레이션으로 돌리면 segfault 가 났다(qemu).

### 3.3 업로드
tokenizer 와 **같은 S3 prefix** 아래에 둔다. benchmark 하나에 `test_data_ref` 는 하나뿐이기 때문이다.
```
s3://kbl-eval/perf/v1/qwen3-1.7b/        ← tokenizer (4장)
s3://kbl-eval/perf/v1/prompts.jsonl      ← 고객 프롬프트
job: test_data_ref.s3 {bucket: kbl-eval, key: perf/v1, secret_ref: kbl-eval-s3}
     data: /test_data/prompts.jsonl,  processor: /test_data/qwen3-1.7b
```

---

## 4. modelcar 이미지에서 tokenizer 꺼내기
모델이 레드햇 modelcar 이미지로 이미 미러되어 있으므로 **안에서 꺼낸다**. 모델 파일은 `/models/` 아래에 있다.
Red Hat modelcar 와 이 프로젝트의 `hf-modelcar`(`model-lifecycle`) 모두 같은 위치를 쓴다.

필요한 파일: `tokenizer.json`, `tokenizer_config.json`, `vocab.json`, `merges.txt`, `config.json`, `generation_config.json`(+ 있으면 `special_tokens_map.json`, `added_tokens.json`, `tokenizer.model`). 가중치(`*.safetensors`)는 필요 없다.

### 4.1 방법 1: `extract-tokenizer.py` (tokenizer 레이어만 받음, 권장)
olot 로 만든 modelcar 는 파일 하나가 레이어 하나이고, 레이어 annotation `olot.layer.content.inlayerpath` 에 경로가 있다. 이 annotation 으로 tokenizer 레이어만 받는다.
```bash
podman login <내부레지스트리>          # auth.json 을 그대로 읽는다 (또는 --authfile)
python3 extract-tokenizer.py <내부레지스트리>/<repo>/modelcar-qwen3-1.7b@sha256:<digest> ./qwen3-1.7b \
  [--ca-file <사내 CA>]
#   config.json                     726 bytes
#   generation_config.json          239 bytes
#   merges.txt                1,671,853 bytes
#   tokenizer.json           11,422,654 bytes
#   tokenizer_config.json         9,732 bytes
#   vocab.json                2,776,833 bytes
```
- 테스트: `quay.io/gunoh/modelcar-qwen3-1.7b` 에서 약 20초에 16MB 만 받았다. 가중치 레이어(3.4GB, 0.6GB)는 받지 않는다.
- 표준 라이브러리만 쓰므로 워크벤치나 bastion 어디서든 실행할 수 있다.
- annotation 이 없는 이미지(레이어 하나에 모델 전체가 든 형식)면 "olot 형식이 아님" 오류를 내고 끝난다. 그때는 방법 2 를 쓴다.

### 4.2 방법 2: `oc image extract` (모든 형식, 이미지 전체를 받음)
```bash
mkdir -p qwen3-1.7b
oc image extract <내부레지스트리>/<repo>/modelcar-qwen3-1.7b@sha256:<digest> \
  -a <auth.json> --confirm \
  --path /models/tokenizer.json:./qwen3-1.7b/ \
  --path /models/tokenizer_config.json:./qwen3-1.7b/ \
  --path /models/vocab.json:./qwen3-1.7b/ \
  --path /models/merges.txt:./qwen3-1.7b/ \
  --path /models/config.json:./qwen3-1.7b/ \
  --path /models/generation_config.json:./qwen3-1.7b/
```
- `--path` 를 **한 번에 모두** 준다. `oc image extract` 는 원하는 파일이 든 레이어만 고르지 않고 모든 레이어를 받으면서 거른다. 파일마다 따로 실행하면 가중치를 파일 수만큼 다시 받는다.
- 대상 디렉터리가 미리 있어야 한다(없으면 실패).
- 대안: `podman create <image>` 로 컨테이너를 만든 뒤 `podman cp <id>:/models/tokenizer.json ./qwen3-1.7b/` 를 쓰고 `podman rm` 으로 지운다(이미지 전체 pull).

### 4.3 확인과 업로드
```bash
python3 -c "from transformers import AutoTokenizer; t=AutoTokenizer.from_pretrained('./qwen3-1.7b'); print(type(t).__name__, len(t('보험료 납입을 멈출 수 있나요?')['input_ids']))"
# Qwen2Tokenizer 14
aws s3 cp --recursive ./qwen3-1.7b s3://kbl-eval/perf/v1/qwen3-1.7b/ --endpoint-url <S3 endpoint>
```
job 파라미터: `processor: /test_data/qwen3-1.7b`, `test_data_ref.s3: {bucket: kbl-eval, key: perf/v1, secret_ref: kbl-eval-s3}`.
adapter v0.7.2 는 `processor` 가 `/test_data/` 아래 디렉터리면 HF offline 환경변수를 켠다.

---

## 5. 합성 데이터
맞다. **GuideLLM 이미지 안에 있다.** 추가로 반입할 것은 없다.
- `data: prompt_tokens=N,output_tokens=M` 이면 GuideLLM 0.5.3 이 `faker` 라이브러리(GuideLLM 의 필수 의존성)로 문장을 만든다(`guidellm/data/deserializers/synthetic.py`). 인터넷을 쓰지 않는다.
- 이미지 안 패키지(2026-11-18 확인): guidellm 0.5.3, faker 40.44.0, transformers 5.17.0, datasets 5.0.1, torch 2.15.0 (`/opt/app-root/lib/python3.12/site-packages`).
- 토큰 수를 정확히 맞추려면 tokenizer 가 필요하다(4장).
- 만들어지는 문장은 **영어**다. 한국어 토큰 분포와는 다르다. 실제 업무 분포가 중요하면 3장의 고객 프롬프트를 쓴다.

---

## 6. 확인하지 않은 것
- 폐쇄망에서의 전체 실행. 연결 환경에서도 `/test_data` tokenizer 방식으로 job 을 돌려 보지는 않았다.
- `guidellm preprocess dataset` 의 실제 변환. 이미지 안에 명령(`guidellm/__main__.py` 의 `preprocess dataset`, `--short-prompt-strategy`)과 실행 파일 `/opt/app-root/bin/guidellm` 이 있는 것만 확인했다(로컬 arm64 에뮬레이션에서는 실행이 segfault).
- `extract-tokenizer.py` 의 Nexus 인증(Basic)과 사내 CA. quay.io 익명 토큰 방식으로만 확인했다.
- 방법 A 이미지 빌드. `registry.redhat.io` 인증이 필요해서 빌드는 하지 않았다. wheel 다운로드는 확인했다.
