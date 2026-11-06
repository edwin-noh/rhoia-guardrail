#!/usr/bin/env python3
"""run.py 결과 디렉터리 → KB Life SLA 리포트 (sla-report.md, sla-report.csv).

지표 출처
  - 평균값(요청/초, 출력 토큰/초, TTFT/ITL 평균): EvalHub job 결과 (results.benchmarks[].metrics)
  - 중앙값/p95(TTFT, E2E 지연)와 완료/오류 요청 수: GuideLLM 이 평가 pod 로그에 출력하는 요약 표
    (adapter v0.5.0 은 benchmarks.json 을 MLflow/OCI 로 올리지 않으므로 로그에서 읽는다.
     pod 가 정리되면 이 값들은 '-' 로 표시된다)

    python3 report.py results/<시각>
"""
from __future__ import annotations

import csv
import json
import re
import subprocess
import sys
from pathlib import Path

DIR = Path(__file__).resolve().parent


def load_yaml_sla() -> dict:
    """scenarios.yaml 의 sla 를 표준 라이브러리만으로 읽는다 (report 는 단독 실행 가능하게)."""
    sla, cur = {}, None
    for line in (DIR / "scenarios.yaml").read_text().splitlines():
        m = re.match(r"\s*-\s*id:\s*(\S+)", line)
        if m:
            cur = m.group(1)
        m = re.match(r"\s*sla:\s*\{(.+)\}", line)
        if m and cur:
            sla[cur] = {k.strip(): v.strip() for k, v in (kv.split(":") for kv in m.group(1).split(","))}
    return sla


def pod_log(job_id: str, tenant: str = "kbl-eval") -> str:
    """job 의 평가 pod(adapter 컨테이너) 로그. pod 이름은 job id 앞 26자로 시작한다."""
    try:
        pods = subprocess.run(["oc", "get", "pods", "-n", tenant, "-o", "name"],
                              capture_output=True, text=True, check=True).stdout.split()
        pod = next(p for p in pods if p.split("/", 1)[1].startswith(job_id[:26]))
        return subprocess.run(["oc", "logs", "-n", tenant, pod, "-c", "adapter"],
                              capture_output=True, text=True, check=True).stdout
    except (StopIteration, subprocess.CalledProcessError, FileNotFoundError):
        return ""


def table_row(log: str, title: str) -> list[str] | None:
    """GuideLLM 요약 표(제목 다음)에서 첫 데이터 행의 셀 목록."""
    lines = [re.sub(r"^.*GuideLLM: ", "", l) for l in log.splitlines()]
    for i, line in enumerate(lines):
        if title in line:
            for row in lines[i + 1:i + 12]:
                cells = [c.strip() for c in row.strip().strip("|").split("|")]
                if row.startswith("|") and cells and cells[0] not in ("", "Benchmark", "Strategy") \
                        and not set(cells[0]) <= set("=-"):
                    return cells
    return None


def num(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def fmt(v, digits=0) -> str:
    return "-" if v is None else f"{v:,.{digits}f}"


def main() -> int:
    out = Path(sys.argv[1])
    sla = load_yaml_sla()
    rows = []
    for res_file in sorted(out.glob("*.result.json"), key=lambda p: p.stat().st_mtime):
        sid = res_file.name.removesuffix(".result.json")
        res = json.loads(res_file.read_text())
        req = json.loads((out / f"{sid}.request.json").read_text())
        p = req["benchmarks"][0]["parameters"]
        bench = (res.get("results", {}).get("benchmarks") or [{}])[0]
        m = bench.get("metrics") or {}
        log = pod_log(res.get("resource", {}).get("id") or res.get("id", ""))

        lat = table_row(log, "Request Latency Statistics")   # E2E Mdn,p95 | TTFT Mdn,p95 | ITL | TPOT
        run = table_row(log, "Run Summary Info")             # ... | 입력토큰 Comp,Inc,Err | 출력토큰 ...
        tok = table_row(log, "Request Token Statistics")     # 입력 토큰/요청 Mdn ...
        e2e_mdn, e2e_p95, ttft_mdn, ttft_p95 = (num(lat[i]) for i in (1, 2, 3, 4)) if lat else (None,) * 4
        ok = err = None
        if run and tok and num(tok[1]):
            per_req = num(tok[1])
            ok, err = round(num(run[6]) / per_req), round(num(run[8]) / per_req)

        rule = sla.get(sid, {})
        metric, value = rule.get("metric"), m.get(rule.get("metric", ""), None)
        if value is None:
            verdict = "판정불가"
        elif "max" in rule:
            verdict = "PASS" if value <= float(rule["max"]) else "FAIL"
        else:
            verdict = "PASS" if value >= float(rule["min"]) else "FAIL"
        criterion = f"{metric} {'≤' if 'max' in rule else '≥'} {rule.get('max', rule.get('min'))}" if rule else "-"

        rows.append({
            "시나리오": sid,
            "부하": f"{p['profile']}{' ×' + str(p['rate']) if 'rate' in p else ''}",
            "토큰(입/출)": p["data"].replace("prompt_tokens=", "").replace(",output_tokens=", "/"),
            "측정(초)": p["max_seconds"],
            "완료/오류 요청": f"{fmt(ok)}/{fmt(err)}",
            "요청/초": fmt(m.get("requests_per_second"), 2),
            "출력 토큰/초": fmt(m.get("output_tokens_per_second"), 1),
            "TTFT 평균(ms)": fmt(m.get("mean_ttft_ms")),
            "TTFT 중앙/p95(ms)": f"{fmt(ttft_mdn)}/{fmt(ttft_p95)}",
            "ITL 평균(ms)": fmt(m.get("mean_itl_ms"), 1),
            "E2E 중앙/p95(s)": f"{fmt(e2e_mdn, 1)}/{fmt(e2e_p95, 1)}",
            "SLA 기준": criterion,
            "결과": verdict if res.get("status", {}).get("state") == "completed" else res.get("status", {}).get("state"),
        })

    if not rows:
        print("결과가 없습니다:", out)
        return 1
    cols = list(rows[0])
    md = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    md += ["| " + " | ".join(str(r[c]) for c in cols) + " |" for r in rows]
    passed = sum(r["결과"] == "PASS" for r in rows)
    summary = f"SLA 통과: {passed}/{len(rows)}"
    (out / "sla-report.md").write_text("\n".join(md) + f"\n\n{summary}\n", encoding="utf-8")
    with (out / "sla-report.csv").open("w", newline="", encoding="utf-8-sig") as f:   # 엑셀 호환(BOM)
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)
    print("\n".join(md))
    print(f"\n{summary}\n리포트: {out / 'sla-report.md'} / sla-report.csv")
    return 0 if passed == len(rows) else 3


if __name__ == "__main__":
    sys.exit(main())
