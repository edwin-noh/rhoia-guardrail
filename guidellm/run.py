#!/usr/bin/env python3
"""KB Life 서빙 성능 평가: scenarios.yaml 의 시나리오를 EvalHub(GuideLLM)로 하나씩 차례로 실행한다.

run.sh 가 config.env 를 읽어 환경변수로 넘겨준다. 직접 실행하지 말고 ./run.sh 를 쓴다.
시나리오를 한 job 에 몰아 넣으면 EvalHub 가 pod 를 동시에 띄워 서로 부하를 간섭하므로,
시나리오마다 job 을 하나씩 제출하고 끝날 때까지 기다린 뒤 다음으로 넘어간다.
"""
from __future__ import annotations

import json
import os
import ssl
import subprocess
import sys
import time
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
        self.token = env("TOKEN") or oc("create", "token", "kbl-eval-runner", "-n", self.tenant, "--duration=2h")
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


def build_job(sc: dict, provider_id: str) -> dict:
    """시나리오 + 설정값 → EvalHub job 요청 본문. 우선순위: 환경변수 > 시나리오 > config.env 기본값."""
    max_seconds = int(os.environ.get("MAX_SECONDS_OVERRIDE") or sc.get("max_seconds") or env("MAX_SECONDS", "30"))
    params: dict = {
        "profile": sc["profile"],
        "max_seconds": max_seconds,
        "data": f"prompt_tokens={sc['prompt_tokens']},output_tokens={sc['output_tokens']}",
        "processor": env("PROCESSOR", "Qwen/Qwen3-4B-Instruct-2507"),
        "request_type": "chat_completions",
        "outputs": "json,csv,yaml",
    }
    if env("WARMUP") and sc["profile"] != "synchronous":
        params["warmup"] = env("WARMUP")
    if "rate" in sc:
        params["rate"] = max(1, round(float(sc["rate"]) * float(env("RATE_SCALE", "1"))))
    if env("MAX_REQUESTS"):
        params["max_requests"] = int(env("MAX_REQUESTS"))
    return {
        "name": f"perf-{sc['id']}",
        "model": {"url": env("MODEL_URL"), "name": env("MODEL_NAME")},
        "experiment": {"name": env("EXPERIMENT", "kbl-qwen3-4b-perf")},
        "tags": ["performance", sc["id"]],
        "benchmarks": [{"provider_id": provider_id, "id": sc["id"], "parameters": params}],
    }


def main() -> int:
    scenarios = yaml.safe_load((DIR / "scenarios.yaml").read_text())["scenarios"]
    wanted = env("SCENARIOS", "all")
    if wanted != "all":
        names = {s.strip() for s in wanted.split(",")}
        unknown = names - {s["id"] for s in scenarios}
        if unknown:
            print(f"알 수 없는 시나리오: {', '.join(sorted(unknown))}", file=sys.stderr)
            return 2
        scenarios = [s for s in scenarios if s["id"] in names]

    provider_id = subprocess.run([str(DIR / "register-provider.sh")], check=True,
                                 capture_output=True, text=True).stdout.strip()
    hub = EvalHub()
    out = DIR / "results" / datetime.now().strftime("%Y%m%d-%H%M%S")
    out.mkdir(parents=True)
    print(f"provider {env('PROVIDER_NAME')}={provider_id}  결과: {out}")
    print(f"설정: MAX_SECONDS={env('MAX_SECONDS')} RATE_SCALE={env('RATE_SCALE')} "
          f"WARMUP={env('WARMUP') or '-'} MAX_REQUESTS={env('MAX_REQUESTS') or '-'}\n")

    for sc in scenarios:
        body = build_job(sc, provider_id)
        (out / f"{sc['id']}.request.json").write_text(json.dumps(body, ensure_ascii=False, indent=2))
        p = body["benchmarks"][0]["parameters"]
        print(f"▶ {sc['id']:<14} {sc['name']}  ({p['profile']}"
              f"{', rate=' + str(p['rate']) if 'rate' in p else ''}, {p['data']}, {p['max_seconds']}s)")
        job = hub.call("POST", "/api/v1/evaluations/jobs", body)
        job_id = (job.get("resource") or {}).get("id") or job.get("id")
        start = time.monotonic()
        while True:
            time.sleep(10)
            res = hub.call("GET", f"/api/v1/evaluations/jobs/{job_id}")
            state = res.get("status", {}).get("state")
            if state in TERMINAL:
                break
        (out / f"{sc['id']}.result.json").write_text(json.dumps(res, ensure_ascii=False, indent=2))
        print(f"  └ {state} ({time.monotonic() - start:.0f}s) job={job_id}")

    print()
    return subprocess.call([sys.executable, str(DIR / "report.py"), str(out)])


if __name__ == "__main__":
    sys.exit(main())
