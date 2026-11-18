#!/usr/bin/env python3
"""고객 질문 원본(CSV / JSONL / TXT) → GuideLLM 용 prompts.jsonl ({"prompt": ...} 한 줄에 하나).

폐쇄망 안에서 실행한다. 표준 라이브러리만 쓴다(워크벤치 기본 이미지에서 바로 실행 가능).
  1) 지정한 열(또는 TXT 의 각 줄)을 읽는다
  2) 개인정보로 보이는 패턴을 마스킹한다 (주민등록번호, 휴대폰·전화, 이메일, 카드번호, 계좌번호 형태 숫자열)
  3) 공백 정리, 너무 짧거나 긴 질문 제외, 중복 제거
  4) --tokenizer 를 주면 토큰 길이 분포(중앙값, p90)를 출력한다 (transformers 가 있을 때만)

정규식 마스킹은 보조 수단이다. 이름·주소 같은 자유 텍스트 개인정보는 잡지 못하므로
반출·공유 전에는 고객사 비식별 절차(검수)를 따른다.

    python3 prepare-prompts.py questions.csv prompts.jsonl --column question
    python3 prepare-prompts.py chat-log.jsonl prompts.jsonl --column user_message --tokenizer /path/qwen3-1.7b
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import statistics
import sys
from pathlib import Path

PII_PATTERNS = [   # (이름, 정규식, 바꿀 값). 위에서부터 차례로 적용한다
    ("주민등록번호", re.compile(r"\b\d{6}\s*-\s*[1-8]\d{6}\b"), "[주민번호]"),
    ("이메일", re.compile(r"\b[\w.+-]+@[\w-]+(\.[\w-]+)+\b"), "[이메일]"),
    ("카드번호", re.compile(r"\b\d{4}[- ]\d{4}[- ]\d{4}[- ]\d{4}\b"), "[카드번호]"),
    ("휴대폰·전화", re.compile(r"\b0\d{1,2}[- .]?\d{3,4}[- .]?\d{4}\b"), "[전화번호]"),
    ("계좌번호 형태", re.compile(r"\b\d{2,6}-\d{2,6}-\d{2,8}(-\d{1,4})?\b"), "[계좌번호]"),
    ("긴 숫자열", re.compile(r"\b\d{10,}\b"), "[번호]"),
]


def read_texts(path: Path, column: str | None) -> list[str]:
    suffix = path.suffix.lower()
    if suffix == ".csv":
        with path.open(encoding="utf-8-sig", newline="") as f:
            rows = list(csv.DictReader(f))
        if not rows or column not in rows[0]:
            sys.exit(f"CSV 에 열 '{column}' 이 없습니다. 열: {list(rows[0]) if rows else []}")
        return [r[column] or "" for r in rows]
    if suffix in (".jsonl", ".ndjson"):
        out = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                out.append(str(json.loads(line).get(column, "")))
        return out
    return path.read_text(encoding="utf-8").splitlines()          # .txt: 한 줄에 질문 하나


def mask(text: str, counts: dict[str, int]) -> str:
    for name, pattern, repl in PII_PATTERNS:
        text, n = pattern.subn(repl, text)
        counts[name] = counts.get(name, 0) + n
    return text


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("input", type=Path)
    ap.add_argument("output", type=Path)
    ap.add_argument("--column", default="prompt", help="CSV/JSONL 에서 질문이 든 열 (기본 prompt)")
    ap.add_argument("--min-chars", type=int, default=5)
    ap.add_argument("--max-chars", type=int, default=8000)
    ap.add_argument("--limit", type=int, default=0, help="앞에서부터 N건만 (0 = 전부)")
    ap.add_argument("--tokenizer", help="토큰 길이 분포를 볼 tokenizer 디렉터리 (선택)")
    a = ap.parse_args()

    counts: dict[str, int] = {}
    seen, prompts = set(), []
    raw = read_texts(a.input, a.column)
    for text in raw:
        text = re.sub(r"\s+", " ", mask(text, counts)).strip()
        if not (a.min_chars <= len(text) <= a.max_chars) or text in seen:
            continue
        seen.add(text)
        prompts.append(text)
        if a.limit and len(prompts) >= a.limit:
            break

    with a.output.open("w", encoding="utf-8") as f:
        for p in prompts:
            f.write(json.dumps({"prompt": p}, ensure_ascii=False) + "\n")
    print(f"입력 {len(raw)}건 → 출력 {len(prompts)}건: {a.output}")
    print("마스킹:", ", ".join(f"{k} {v}" for k, v in counts.items() if v) or "없음")

    if a.tokenizer:
        try:
            from transformers import AutoTokenizer
        except ImportError:
            print("transformers 가 없어 토큰 분포를 건너뜁니다")
            return 0
        tok = AutoTokenizer.from_pretrained(a.tokenizer)
        lens = sorted(len(tok(p)["input_ids"]) for p in prompts)
        if lens:
            p90 = lens[min(len(lens) - 1, int(len(lens) * 0.9))]
            print(f"토큰 길이: 중앙값 {statistics.median(lens):.0f}, p90 {p90}, 최대 {lens[-1]}"
                  " → scenarios.yaml 의 prompt_tokens 와 모델 max_model_len 을 맞출 때 참고")
    return 0


if __name__ == "__main__":
    sys.exit(main())
