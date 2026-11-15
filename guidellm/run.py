#!/usr/bin/env python3
"""KB Life 서빙 성능 평가: targets.yaml 의 대상 × scenarios.yaml 의 시나리오를 EvalHub(GuideLLM)로 하나씩 차례로 실행한다.

run.sh 가 config.env 를 읽어 환경변수로 넘겨준다. 직접 실행하지 말고 ./run.sh 를 쓴다.
- 한 job 에 benchmark 를 여러 개 넣으면 EvalHub 가 pod 를 동시에 띄워 서로 부하를 간섭한다.
- Raw 와 MaaS 는 같은 GPU 를 나눠 쓸 수 있다.
그래서 (대상, 시나리오)마다 job 을 하나씩 제출하고 끝날 때까지 기다린 뒤 다음으로 넘어간다.
"""
from __future__ import annotations

import json
import os
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

import yaml

DIR = Path(__file__).resolve().parent
TERMINAL = {"completed", "failed", "cancelled", "partially_failed"}


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def oc(*args: str) -> str:
    return subprocess.run(["oc", *args], check=True, capture_output=True, text=True).stdout.strip()


class EvalHub:
    def __init__(self) -> None:
        self.tenant = env("TENANT", "kbl-eval")
        self.url = env("EVALHUB_URL") or "https://" + oc(
            "get", "route", "evalhub", "-n", "redhat-ods-applications", "-o", "jsonpath={.spec.host}")
        self.token = env("TOKEN") or oc("create", "token", "kbl-eval-runner", "-n", self.tenant, "--duration=4h")
        self.ctx = ssl._create_unverified_context()  # 라우터 인증서가 사설 CA 인 lab 환경

    def call(self, method: str, path: str, body: dict | None = None) -> dict:
        req = urllib.request.Request(
            self.url + path, method=method,
            data=json.dumps(body).encode() if body is not None else None,
            headers={"Authorization": f"Bearer {self.token}", "X-Tenant": self.tenant,
                     "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, context=self.ctx, timeout=60) as r:
                return json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"{method} {path} → {e.code}: {e.read().decode()[:500]}") from e


def pick(items: list[dict], wanted: str, kind: str) -> list[dict]:
    """쉼표로 구분한 id 목록(또는 all)으로 항목을 고른다."""
    if wanted == "all":
        return items
    names = [s.strip() for s in wanted.split(",") if s.strip()]
    unknown = set(names) - {i["id"] for i in items}
    if unknown:
        sys.exit(f"알 수 없는 {kind}: {', '.join(sorted(unknown))}")
    return [i for i in items if i["id"] in names]


def build_job(tg: dict, sc: dict, provider_id: str) -> dict:
    """대상 + 시나리오 + 설정값 → EvalHub job 요청 본문. 우선순위: 환경변수 > 시나리오 > config.env 기본값."""
    max_seconds = int(os.environ.get("MAX_SECONDS_OVERRIDE") or sc.get("max_seconds") or env("MAX_SECONDS", "30"))
    # data: 시나리오에 data(파일 경로 등)가 있으면 그대로, 없으면 토큰 길이로 합성 데이터
    data = sc.get("data") or f"prompt_tokens={sc['prompt_tokens']},output_tokens={sc['output_tokens']}"
    params: dict = {
        "profile": sc["profile"],
        "max_seconds": max_seconds,
        "data": data,
        "processor": sc.get("processor") or tg["processor"],
        "request_type": "chat_completions",
        "outputs": "json,csv,yaml",   # html 은 템플릿을 인터넷에서 받으므로 폐쇄망에서 실패한다
    }
    for key in ("data_args", "data_column_mapper", "data_samples"):
        if key in sc:
            params[key] = sc[key]
    if env("WARMUP") and sc["profile"] != "synchronous":
        params["warmup"] = env("WARMUP")
    if "rate" in sc:
        params["rate"] = max(1, round(float(sc["rate"]) * float(env("RATE_SCALE", "1"))))
    if env("MAX_REQUESTS"):
        params["max_requests"] = int(env("MAX_REQUESTS"))

    bench = {"provider_id": provider_id, "id": sc["id"], "parameters": params}
    if "test_data_ref" in sc:            # 고객 프롬프트 파일(S3/PVC) → pod 의 /test_data
        bench["test_data_ref"] = sc["test_data_ref"]
    model = {"url": tg["url"], "name": tg["model"]}
    if tg.get("auth_secret"):
        model["auth"] = {"secret_ref": tg["auth_secret"]}
    return {
        "name": f"perf-{tg['id']}-{sc['id']}".replace("_", "-"),
        "model": model,
        "experiment": {"name": tg["experiment"]},   # 있어야 adapter 가 MLflow 에 기록한다
        "tags": ["performance", tg["id"], sc["id"]],
        "benchmarks": [bench],
    }


def main() -> int:
    targets = pick(yaml.safe_load((DIR / "targets.yaml").read_text())["targets"], env("TARGETS", "all"), "대상")
    scenarios = pick(yaml.safe_load((DIR / "scenarios.yaml").read_text())["scenarios"], env("SCENARIOS", "all"),
                     "시나리오")

    provider_id = subprocess.run([str(DIR / "register-provider.sh")], check=True,
                                 capture_output=True, text=True).stdout.strip()
    hub = EvalHub()
    out = DIR / "results" / datetime.now().strftime("%Y%m%d-%H%M%S")
    out.mkdir(parents=True)
    print(f"provider {env('PROVIDER_NAME')}={provider_id}  결과: {out}")
    print(f"대상: {', '.join(t['id'] for t in targets)}  시나리오: {', '.join(s['id'] for s in scenarios)}")
    print(f"설정: MAX_SECONDS={os.environ.get('MAX_SECONDS_OVERRIDE') or env('MAX_SECONDS')} "
          f"RATE_SCALE={env('RATE_SCALE')} WARMUP={env('WARMUP') or '-'} MAX_REQUESTS={env('MAX_REQUESTS') or '-'}\n")

    for tg in targets:
        print(f"■ {tg['id']}: {tg['name']}  {tg['url']}")
        for sc in scenarios:
            key = f"{tg['id']}.{sc['id']}"
            need = int(sc.get("prompt_tokens", 0)) + int(sc.get("output_tokens", 0))
            if tg.get("max_model_len") and need > int(tg["max_model_len"]):
                print(f"  - {sc['id']:<14} 건너뜀: 입력+출력 {need} 토큰 > max_model_len {tg['max_model_len']}")
                continue
            body = build_job(tg, sc, provider_id)
            (out / f"{key}.request.json").write_text(json.dumps(body, ensure_ascii=False, indent=2))
            p = body["benchmarks"][0]["parameters"]
            print(f"  ▶ {sc['id']:<14} {sc['name']}  ({p['profile']}"
                  f"{', rate=' + str(p['rate']) if 'rate' in p else ''}, {p['data']}, {p['max_seconds']}s)")
            try:
                job = hub.call("POST", "/api/v1/evaluations/jobs", body)
            except RuntimeError as e:
                print(f"    └ 제출 실패: {e}")
                continue
            job_id = (job.get("resource") or {}).get("id") or job.get("id")
            start = time.monotonic()
            while True:
                time.sleep(10)
                res = hub.call("GET", f"/api/v1/evaluations/jobs/{job_id}")
                state = res.get("status", {}).get("state")
                if state in TERMINAL:
                    break
            (out / f"{key}.result.json").write_text(json.dumps(res, ensure_ascii=False, indent=2))
            msg = "" if state == "completed" else f"  {res.get('status', {}).get('message', {}).get('message', '')}"
            print(f"    └ {state} ({time.monotonic() - start:.0f}s) job={job_id}{msg}")
        print()

    return subprocess.call([sys.executable, "-I", str(DIR / "report.py"), str(out)])


if __name__ == "__main__":
    sys.exit(main())
