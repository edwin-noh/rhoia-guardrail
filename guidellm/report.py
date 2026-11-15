#!/usr/bin/env python3
"""run.py 결과 디렉터리 → KB Life 성능 리포트 (sla-report.md, sla-report.csv).

지표 출처
  - 분포(TTFT·E2E·ITL 중앙/p95), 완료/오류 요청 수, 측정 구간: GuideLLM 의 benchmarks.json.
    adapter v0.7.2 가 benchmark 마다 MLflow run 의 artifact 로 올린다. 처음 한 번 받아서
    결과 폴더에 <대상>.<시나리오>.benchmarks.json 으로 저장하고 다음부터는 그 파일을 쓴다.
  - benchmarks.json 을 받지 못하면 EvalHub job 결과(results.benchmarks[].metrics)의 평균값만 쓴다.

판정은 scenarios.yaml 의 sla(예: p95_ttft_ms ≤ 1500)로 한다. EvalHub 서버도 provider 의
pass_criteria(평균값 기준)로 따로 판정하므로 두 결과가 다를 수 있다. 리포트는 p95 판정을 따른다.

    uv run --no-project --with pyyaml python -I report.py results/<시각>
"""
from __future__ import annotations

import csv
import json
import os
import ssl
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

import yaml

DIR = Path(__file__).resolve().parent
TENANT = os.environ.get("TENANT", "kbl-eval")
MLFLOW_URL = os.environ.get("MLFLOW_URL", "https://rh-ai.apps.rhoai-35.edwin.home/mlflow").rstrip("/")
GRAFANA_URL = os.environ.get("GRAFANA_URL", "https://grafana-route-grafana.apps.rhoai-35.edwin.home").rstrip("/")
LINK_MARGIN_MS = 60_000   # 대시보드 링크 시간 범위: 측정 구간 앞뒤로 1분


# ---------------------------------------------------------------- MLflow
_token: str | None = None


def token() -> str:
    global _token
    if _token is None:
        _token = os.environ.get("TOKEN") or subprocess.run(
            ["oc", "create", "token", "kbl-eval-runner", "-n", TENANT, "--duration=1h"],
            check=True, capture_output=True, text=True).stdout.strip()
    return _token


def fetch_benchmarks(run_id: str) -> dict | None:
    """MLflow run 의 benchmarks.json. RHOAI MLflow 는 X-MLFLOW-WORKSPACE(=테넌트 네임스페이스) 헤더가 필요하다."""
    url = f"{MLFLOW_URL}/get-artifact?" + urllib.parse.urlencode({"run_id": run_id, "path": "benchmarks.json"})
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token()}", "X-MLFLOW-WORKSPACE": TENANT})
    try:
        with urllib.request.urlopen(req, context=ssl._create_unverified_context(), timeout=60) as r:
            return json.loads(r.read())
    except (urllib.error.URLError, json.JSONDecodeError, subprocess.CalledProcessError, OSError) as e:
        print(f"  (benchmarks.json 을 받지 못함 run={run_id}: {e})", file=sys.stderr)
        return None


def load_benchmarks(out: Path, key: str, run_id: str | None) -> dict | None:
    cache = out / f"{key}.benchmarks.json"
    if cache.exists():
        return json.loads(cache.read_text())
    if not run_id:
        return None
    data = fetch_benchmarks(run_id)
    if data:
        cache.write_text(json.dumps(data))
    return data


# ---------------------------------------------------------------- 지표 추출
def dist(metrics: dict, name: str) -> dict:
    return (metrics.get(name) or {}).get("successful") or {}


def pct(d: dict, p: str) -> float | None:
    return (d.get("percentiles") or {}).get(p)


def extract(res: dict, bm: dict | None) -> dict:
    """job 결과 + benchmarks.json → 리포트에 쓰는 지표 이름으로 정리 (없는 값은 None)."""
    ev = ((res.get("results") or {}).get("benchmarks") or [{}])[0].get("metrics") or {}
    m: dict = {
        "requests_per_second": ev.get("requests_per_second"),
        "output_tokens_per_second": ev.get("output_tokens_per_second"),
        "mean_ttft_ms": ev.get("mean_ttft_ms"),
        "mean_itl_ms": ev.get("mean_itl_ms"),
    }
    if not bm or not bm.get("benchmarks"):
        return m
    b = bm["benchmarks"][0]   # 단일 단계 profile 만 쓴다 (sweep 같은 다단계는 첫 단계)
    bmm = b.get("metrics") or {}
    ttft, e2e, itl = dist(bmm, "time_to_first_token_ms"), dist(bmm, "request_latency"), dist(bmm, "inter_token_latency_ms")
    totals = bmm.get("request_totals") or {}
    ok, err, inc = (totals.get(k) or 0 for k in ("successful", "errored", "incomplete"))
    m.update({
        "mean_ttft_ms": ttft.get("mean", m["mean_ttft_ms"]),
        "p50_ttft_ms": pct(ttft, "p50"),
        "p95_ttft_ms": pct(ttft, "p95"),
        "p50_e2e_s": pct(e2e, "p50"),
        "p95_e2e_s": pct(e2e, "p95"),
        "mean_itl_ms": itl.get("mean", m["mean_itl_ms"]),
        "p95_itl_ms": pct(itl, "p95"),
        "mean_concurrency": dist(bmm, "request_concurrency").get("mean"),
        "ok": ok, "errored": err, "incomplete": inc,
        "error_rate": (err / (ok + err)) if (ok + err) else None,
        "start_ms": int(b["start_time"] * 1000) if b.get("start_time") else None,
        "end_ms": int(b["end_time"] * 1000) if b.get("end_time") else None,
    })
    return m


def judge(rule: dict, m: dict, state: str | None) -> tuple[str, str]:
    """(판정, 기준 문자열)."""
    if not rule:
        return ("-" if state == "completed" else state or "-"), "-"
    metric = rule["metric"]
    op, limit = ("≤", float(rule["max"])) if "max" in rule else ("≥", float(rule["min"]))
    criterion = f"{metric} {op} {limit:g}"
    if state != "completed":
        return state or "-", criterion
    value = m.get(metric)
    if value is None:
        return "판정불가", criterion
    if "ttft" in metric and value == 0:   # reasoning parser 모델을 chat 으로 재면 TTFT 가 0 으로 기록된다
        return "판정불가 (TTFT 0: targets.yaml request_type 확인)", criterion
    ok = value <= limit if op == "≤" else value >= limit
    return ("PASS" if ok else "FAIL"), criterion


def fmt(v, digits=0) -> str:
    return "-" if v is None else f"{v:,.{digits}f}"


def md_table(rows: list[dict], cols: list[str]) -> list[str]:
    lines = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    lines += ["| " + " | ".join(str(r.get(c, "")) for c in cols) + " |" for r in rows]
    return lines


# ---------------------------------------------------------------- main
def main() -> int:
    out = Path(sys.argv[1])
    scenarios = {s["id"]: s for s in yaml.safe_load((DIR / "scenarios.yaml").read_text())["scenarios"]}
    targets = {t["id"]: t for t in yaml.safe_load((DIR / "targets.yaml").read_text())["targets"]}

    rows: list[dict] = []
    for res_file in sorted(out.glob("*.result.json"), key=lambda p: p.stat().st_mtime):
        key = res_file.name.removesuffix(".result.json")
        tid, _, sid = key.rpartition(".")          # 예전 결과(<시나리오>.result.json)는 대상 없음
        res = json.loads(res_file.read_text())
        req = json.loads((out / f"{key}.request.json").read_text())
        p = req["benchmarks"][0]["parameters"]
        state = (res.get("status") or {}).get("state")
        run_id = (((res.get("results") or {}).get("benchmarks") or [{}])[0]).get("mlflow_run_id")
        m = extract(res, load_benchmarks(out, key, run_id) if state == "completed" else None)
        verdict, criterion = judge((scenarios.get(sid) or {}).get("sla") or {}, m, state)
        if verdict in ("PASS", "FAIL") and m.get("errored"):
            verdict += f" (오류 {m['errored']}건)"
        data = p["data"]
        if data.startswith("prompt_tokens="):
            data = data.replace("prompt_tokens=", "").replace(",output_tokens=", "/")
        rows.append({
            "대상": tid or "-", "시나리오": sid,
            "부하": f"{p['profile']}{' ×' + str(p['rate']) if 'rate' in p else ''}",
            "데이터(입/출 토큰)": data, "측정(초)": p["max_seconds"],
            "완료/오류 요청": f"{fmt(m.get('ok'))}/{fmt(m.get('errored'))}",
            "평균 동시성": fmt(m.get("mean_concurrency"), 1),
            "요청/초": fmt(m.get("requests_per_second"), 2),
            "출력 토큰/초": fmt(m.get("output_tokens_per_second"), 1),
            "TTFT 평균(ms)": fmt(m.get("mean_ttft_ms")),
            "TTFT 중앙/p95(ms)": f"{fmt(m.get('p50_ttft_ms'))}/{fmt(m.get('p95_ttft_ms'))}",
            "ITL 평균/p95(ms)": f"{fmt(m.get('mean_itl_ms'), 1)}/{fmt(m.get('p95_itl_ms'), 1)}",
            "E2E 중앙/p95(s)": f"{fmt(m.get('p50_e2e_s'), 1)}/{fmt(m.get('p95_e2e_s'), 1)}",
            "SLA 기준": criterion, "결과": verdict,
            "_m": m, "_job": (res.get("resource") or {}).get("id") or res.get("id", ""),
            "_exp": (res.get("resource") or {}).get("mlflow_experiment_id"), "_run": run_id,
        })

    if not rows:
        print("결과가 없습니다:", out)
        return 1

    cols = [c for c in rows[0] if not c.startswith("_")]
    passed = sum(r["결과"].startswith("PASS") for r in rows)
    md = [f"# 서빙 성능 리포트 ({out.name})", "",
          "판정은 scenarios.yaml 의 sla 기준(p95 등). 분포 값은 GuideLLM benchmarks.json(MLflow artifact)에서 읽었다.", ""]
    md += md_table(rows, cols) + ["", f"**SLA 통과: {passed}/{len(rows)}**", ""]

    # Raw vs MaaS 등 대상 비교 (같은 시나리오를 2개 이상 대상에서 돌린 경우)
    tids = list(dict.fromkeys(r["대상"] for r in rows if r["대상"] != "-"))
    if len(tids) > 1:
        by = {(r["대상"], r["시나리오"]): r["_m"] for r in rows}
        sids = list(dict.fromkeys(r["시나리오"] for r in rows))
        base = tids[0]
        cmp_rows = []
        for sid in sids:
            row = {"시나리오": sid}
            for t in tids:
                mm = by.get((t, sid), {})
                row[f"{t} TTFT p95(ms)"] = fmt(mm.get("p95_ttft_ms"))
                row[f"{t} E2E p95(s)"] = fmt(mm.get("p95_e2e_s"), 2)
                row[f"{t} 출력 토큰/초"] = fmt(mm.get("output_tokens_per_second"), 1)
            for t in tids[1:]:
                a, b = by.get((base, sid), {}).get("p95_ttft_ms"), by.get((t, sid), {}).get("p95_ttft_ms")
                row[f"TTFT p95 차이({t}-{base}, ms)"] = fmt(b - a) if a is not None and b is not None else "-"
            cmp_rows.append(row)
        md += [f"## 대상 비교 ({' vs '.join(tids)})", ""] + md_table(cmp_rows, list(cmp_rows[0])) + [""]

    # 결과와 모니터링 링크
    md += ["## 링크", "",
           f"- MLflow: {MLFLOW_URL} (workspace `{TENANT}`), Dashboard: Develop & train → Evaluations (프로젝트 `{TENANT}`)", ""]
    for r in rows:
        m, tg = r["_m"], targets.get(r["대상"], {})
        line = f"- **{r['대상']} / {r['시나리오']}**: job `{r['_job']}`"
        if r["_run"]:
            line += f", MLflow experiment `{r['_exp']}` run `{r['_run']}`"
        if m.get("start_ms") and tg.get("dashboards"):
            frm, to = m["start_ms"] - LINK_MARGIN_MS, m["end_ms"] + LINK_MARGIN_MS
            when = f"{datetime.fromtimestamp(m['start_ms'] / 1000):%H:%M:%S}~{datetime.fromtimestamp(m['end_ms'] / 1000):%H:%M:%S}"
            links = ", ".join(f"[{name}]({url.format(grafana=GRAFANA_URL, **{'from': frm, 'to': to})})"
                              for name, url in tg["dashboards"].items())
            line += f"\n  - 측정 {when}: {links}"
        md.append(line)

    (out / "sla-report.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    with (out / "sla-report.csv").open("w", newline="", encoding="utf-8-sig") as f:   # 엑셀 호환(BOM)
        w = csv.DictWriter(f, fieldnames=cols + ["job_id", "mlflow_run_id"])
        w.writeheader()
        for r in rows:
            w.writerow({**{c: r[c] for c in cols}, "job_id": r["_job"], "mlflow_run_id": r["_run"] or ""})
    print("\n".join(md))
    print(f"\n리포트: {out / 'sla-report.md'} / sla-report.csv")
    return 0 if passed == len(rows) else 3


if __name__ == "__main__":
    sys.exit(main())
