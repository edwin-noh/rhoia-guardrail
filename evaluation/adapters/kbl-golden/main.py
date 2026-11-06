"""KB Life 골든셋 평가 adapter (EvalHub BYOF, eval-hub-sdk FrameworkAdapter).

흐름: /test_data/*.jsonl 읽기 → 문항별 모델 호출(OpenAI chat) → 채점(scoring.py)
      → (선택) LLM-as-judge → 지표/문항별 결과를 EvalHub 와 MLflow 에 보고

job 의 benchmark parameters (모두 선택):
  system_prompt    모델에 줄 system 메시지 (기본: KB라이프 상담원 역할)
  max_tokens       답변 최대 토큰 (기본 512)
  temperature      (기본 0.0)
  judge            LLM-as-judge 사용 여부 (기본 true). false 면 키워드/정답 일치만으로 채점
  judge_url        판정 모델 OpenAI 호환 base URL (/v1 까지). 기본: 평가 대상 모델과 동일
  judge_model      판정 모델 이름. 기본: 평가 대상 모델과 동일
  concurrency      동시 요청 수 (기본 2)
  dataset_version  결과에 남길 골든셋 버전 표시 (예: v1)
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

import httpx
from evalhub.adapter import (
    FrameworkAdapter,
    JobCallbacks,
    JobPhase,
    JobResults,
    JobSpec,
    JobStatusUpdate,
    MessageInfo,
    resolve_model_credentials,
)
from evalhub.adapter.callbacks import DefaultCallbacks
from evalhub.adapter.mlflow import MlflowArtifact
from evalhub.models import EvaluationResult, JobStatus

import scoring

logger = logging.getLogger("kbl-golden")

TEST_DATA_DIR = Path(os.environ.get("KBL_TEST_DATA_DIR", "/test_data"))
DEFAULT_SYSTEM_PROMPT = (
    "당신은 KB라이프생명 보험 상담 AI입니다. 고객 질문에 정확하고 간결하게 한국어로 답하세요. "
    "확실하지 않은 내용은 단정하지 말고 약관 확인이나 상담원 연결을 안내하세요."
)
MCQ_INSTRUCTION = "\n\n보기 중 정답 하나를 골라 '정답: X' 형식으로만 답하세요."


def _tls_verify() -> str | bool:
    """사설 인증서(클러스터 service CA 등)를 신뢰하기 위한 CA 경로를 고른다."""
    for cand in (
        os.environ.get("SSL_CERT_FILE"),
        os.environ.get("REQUESTS_CA_BUNDLE"),
        "/var/run/secrets/kubernetes.io/serviceaccount/service-ca.crt",
    ):
        if cand and Path(cand).is_file():
            return cand
    return True


class ChatClient:
    """OpenAI 호환 /chat/completions 호출기."""

    def __init__(self, base_url: str, model: str, api_key: str | None):
        self.url = base_url.rstrip("/")
        if not self.url.endswith("/chat/completions"):
            self.url += "/chat/completions"
        self.model = model
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self.http = httpx.Client(headers=headers, timeout=180, verify=_tls_verify())

    def chat(self, messages: list[dict], max_tokens: int, temperature: float) -> str:
        last_err: Exception | None = None
        for attempt in range(3):
            try:
                r = self.http.post(self.url, json={
                    "model": self.model, "messages": messages,
                    "max_tokens": max_tokens, "temperature": temperature,
                })
                r.raise_for_status()
                return r.json()["choices"][0]["message"]["content"] or ""
            except Exception as e:  # 네트워크 일시 오류는 재시도
                last_err = e
                time.sleep(2 * (attempt + 1))
        raise RuntimeError(f"model call failed: {last_err}")


def load_golden_set(root: Path) -> tuple[list[dict], str]:
    files = sorted(root.rglob("*.jsonl"))
    if not files:
        raise FileNotFoundError(f"{root} 아래에 골든셋(*.jsonl)이 없습니다. test_data_ref 를 확인하세요.")
    digest = hashlib.sha256()
    items: list[dict] = []
    for f in files:
        data = f.read_bytes()
        digest.update(data)
        items += [json.loads(line) for line in data.decode("utf-8").splitlines() if line.strip()]
    return items, digest.hexdigest()[:16]


class GoldenSetAdapter(FrameworkAdapter):
    def run_benchmark_job(self, config: JobSpec, callbacks: JobCallbacks) -> JobResults:
        start = time.monotonic()
        params = config.parameters if isinstance(config.parameters, dict) else {}
        status = lambda phase: callbacks.report_status(  # noqa: E731
            JobStatusUpdate(status=JobStatus.RUNNING, phase=phase))

        status(JobPhase.INITIALIZING)
        creds = resolve_model_credentials()
        model = ChatClient(str(config.model.url), config.model.name, creds.api_key)
        use_judge = str(params.get("judge", "true")).lower() != "false"
        judge = ChatClient(
            str(params.get("judge_url") or config.model.url),
            str(params.get("judge_model") or config.model.name),
            creds.api_key,
        ) if use_judge else None
        system_prompt = params.get("system_prompt", DEFAULT_SYSTEM_PROMPT)
        max_tokens = int(params.get("max_tokens", 512))
        temperature = float(params.get("temperature", 0.0))

        status(JobPhase.LOADING_DATA)
        items, data_hash = load_golden_set(TEST_DATA_DIR)
        if config.num_examples:
            items = items[: config.num_examples]
        logger.info("golden set: %d items (sha256=%s)", len(items), data_hash)

        status(JobPhase.RUNNING_EVALUATION)

        def evaluate(item: dict) -> scoring.ItemResult:
            prompt = item["question"]
            if item["type"] == "mcq":
                prompt += "\n" + "\n".join(f"{k}. {v}" for k, v in item["choices"].items()) + MCQ_INSTRUCTION
            try:
                response = model.chat(
                    [{"role": "system", "content": system_prompt}, {"role": "user", "content": prompt}],
                    max_tokens, temperature)
            except Exception as e:
                logger.warning("%s: model error %s", item["id"], e)
                return scoring.ItemResult(id=item["id"], category=item["category"], type=item["type"],
                                          question=item["question"], response="", score=0.0, error=str(e))
            judge_score = None
            if judge is not None and item["type"] == "open":
                try:
                    verdict = judge.chat([{"role": "user", "content": scoring.JUDGE_PROMPT.format(
                        question=item["question"], reference=item["reference_answer"], response=response)}],
                        16, 0.0)
                    judge_score = scoring.parse_judge(verdict)
                except Exception as e:
                    logger.warning("%s: judge error %s", item["id"], e)
            return scoring.score_item(item, response, judge_score)

        with ThreadPoolExecutor(max_workers=int(params.get("concurrency", 2))) as pool:
            results = list(pool.map(evaluate, items))

        status(JobPhase.POST_PROCESSING)
        metrics = scoring.aggregate(results)
        errors = sum(1 for r in results if r.error)
        logger.info("metrics: %s (model errors: %d)", metrics, errors)
        if errors == len(results):
            raise RuntimeError("모든 문항에서 모델 호출이 실패했습니다. model.url/인증을 확인하세요.")

        status(JobPhase.PERSISTING_ARTIFACTS)
        self.artifacts = [
            MlflowArtifact("golden/item_results.jsonl",
                           "\n".join(json.dumps(asdict(r), ensure_ascii=False) for r in results).encode(),
                           "application/x-ndjson"),
            MlflowArtifact("golden/summary.json",
                           json.dumps({"metrics": metrics, "items": len(results), "model_errors": errors,
                                       "dataset_sha256": data_hash,
                                       "dataset_version": params.get("dataset_version")},
                                      ensure_ascii=False, indent=2).encode(),
                           "application/json"),
        ]

        return JobResults(
            id=config.id,
            benchmark_id=config.benchmark_id,
            benchmark_index=config.benchmark_index,
            model_name=config.model.name,
            results=[EvaluationResult(metric_name=k, metric_value=v, num_samples=len(results))
                     for k, v in metrics.items()],
            overall_score=metrics.get("golden_score"),
            num_examples_evaluated=len(results),
            duration_seconds=time.monotonic() - start,
            completed_at=datetime.now(UTC),
            evaluation_metadata={
                "framework": "kbl-golden", "version": "1.0",
                "dataset_sha256": data_hash, "dataset_version": params.get("dataset_version"),
                "judge_model": judge.model if judge else None, "model_errors": errors,
            },
        )


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    adapter = GoldenSetAdapter()
    callbacks = DefaultCallbacks.from_adapter(adapter)
    try:
        results = adapter.run_benchmark_job(adapter.job_spec, callbacks)
        run_id = callbacks.mlflow.save(results, adapter.job_spec, artifacts=getattr(adapter, "artifacts", None))
        if run_id:
            results.mlflow_run_id = run_id
            logger.info("MLflow run: %s", run_id)
    except Exception as e:
        logger.exception("golden set job failed")
        callbacks.report_status(JobStatusUpdate(
            status=JobStatus.FAILED, error_message=MessageInfo(message=str(e), message_code="error")))
        return
    callbacks.report_results(results)


if __name__ == "__main__":
    main()
