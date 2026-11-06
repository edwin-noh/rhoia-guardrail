#!/usr/bin/env python3
"""골든셋 CSV(업무 담당자 작성) → JSONL(평가 adapter 입력) 변환 + 스키마 검증.

사용법:
    python3 csv-to-jsonl.py kbl-insurance-qa-v1.csv -o out/kbl-insurance-qa-v1.jsonl

CSV 컬럼 (template.csv 참고):
    id, category, type(open|mcq), question,
    choices("A. ..|B. ..", mcq 전용), answer(A~D, mcq 전용),
    reference_answer(open 필수), required_keywords(";" 구분), forbidden(";" 구분),
    source(근거), owner(작성부서), reviewed_at(YYYY-MM-DD)

검증에 실패하면 행 번호와 이유를 출력하고 종료코드 1 로 끝난다 (CI 에서 사용 가능).
표준 라이브러리만 사용한다.
"""
import argparse
import csv
import json
import re
import sys
from pathlib import Path

REQUIRED = ["id", "category", "type", "question", "source", "owner", "reviewed_at"]
TYPES = {"open", "mcq"}
CHOICE_RE = re.compile(r"^([A-Z])\.\s*(.+)$")


def split_list(value: str) -> list[str]:
    return [v.strip() for v in (value or "").split(";") if v.strip()]


def convert_row(row: dict, line: int) -> tuple[dict | None, list[str]]:
    errors: list[str] = []
    row = {k: (v or "").strip() for k, v in row.items()}
    for key in REQUIRED:
        if not row.get(key):
            errors.append(f"{line}행: '{key}' 가 비어 있음")
    qtype = row.get("type", "")
    if qtype not in TYPES:
        errors.append(f"{line}행: type 은 open 또는 mcq 여야 함 (현재 '{qtype}')")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", row.get("reviewed_at", "")):
        errors.append(f"{line}행: reviewed_at 은 YYYY-MM-DD 형식이어야 함")

    item = {
        "id": row.get("id"),
        "category": row.get("category"),
        "type": qtype,
        "question": row.get("question"),
        "source": row.get("source"),
        "owner": row.get("owner"),
        "reviewed_at": row.get("reviewed_at"),
    }
    if qtype == "open":
        if not row.get("reference_answer"):
            errors.append(f"{line}행: open 문항은 reference_answer 가 필요함")
        item["reference_answer"] = row.get("reference_answer")
        item["required_keywords"] = split_list(row.get("required_keywords", ""))
        item["forbidden"] = split_list(row.get("forbidden", ""))
    elif qtype == "mcq":
        choices = {}
        for part in (row.get("choices") or "").split("|"):
            m = CHOICE_RE.match(part.strip())
            if m:
                choices[m.group(1)] = m.group(2).strip()
        if len(choices) < 2:
            errors.append(f"{line}행: mcq 문항은 'A. ..|B. ..' 형식의 보기가 2개 이상 필요함")
        answer = row.get("answer", "").upper()
        if answer not in choices:
            errors.append(f"{line}행: answer '{answer}' 가 보기({','.join(choices)})에 없음")
        item["choices"] = choices
        item["answer"] = answer
    return (None if errors else item), errors


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("csv", type=Path)
    ap.add_argument("-o", "--output", type=Path, required=True)
    args = ap.parse_args()

    items, errors, seen = [], [], set()
    with args.csv.open(encoding="utf-8-sig", newline="") as f:  # 엑셀 저장 CSV(BOM) 허용
        for line, row in enumerate(csv.DictReader(f), start=2):
            item, errs = convert_row(row, line)
            errors += errs
            if item:
                if item["id"] in seen:
                    errors.append(f"{line}행: id '{item['id']}' 중복")
                seen.add(item["id"])
                items.append(item)

    if errors:
        print("검증 실패:", *errors, sep="\n  - ", file=sys.stderr)
        return 1

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as f:
        for item in items:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")

    by_type: dict[str, int] = {}
    by_cat: dict[str, int] = {}
    for it in items:
        by_type[it["type"]] = by_type.get(it["type"], 0) + 1
        by_cat[it["category"]] = by_cat.get(it["category"], 0) + 1
    print(f"OK: {len(items)}문항 → {args.output}")
    print(f"  유형: {by_type}")
    print(f"  분류: {by_cat}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
