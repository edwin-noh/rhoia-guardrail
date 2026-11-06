#!/usr/bin/env python3
"""KMMLU 과목 + tokenizer 를 EvalHub lm-eval adapter 의 오프라인 /test_data 레이아웃으로 내려받는다.

레이아웃 (adapter 가 자동으로 HF offline 모드로 전환하는 조건):
    <out>/tokenizer/                 parameters.tokenizer=/test_data/tokenizer 로 지정
    <out>/datasets/ , <out>/hub/     HF cache (HF_HOME=/test_data). lm-eval 이 여기서 데이터셋을 읽는다
    <out>/kmmlu-<subject>/dataset_dict.json   tokenizer 옆의 DatasetDict 폴더 = 오프라인 판단 근거

주의: cache 형식은 datasets 버전에 묶여 있으므로 adapter 이미지와 같은 datasets==5.0.1 로 실행한다
      (stage-to-s3.sh 가 uv 로 버전을 고정해 실행한다).
"""
import os
import sys
from pathlib import Path

out = Path(sys.argv[1]).resolve()
subjects = sys.argv[2].split(",")
tokenizer_id = sys.argv[3]
os.environ.update(HF_HOME=str(out), HF_HUB_CACHE=str(out / "hub"), HF_DATASETS_CACHE=str(out / "datasets"))

from datasets import load_dataset  # noqa: E402  (HF_* 설정 후 import)
from transformers import AutoTokenizer  # noqa: E402

AutoTokenizer.from_pretrained(tokenizer_id).save_pretrained(out / "tokenizer")
print(f"tokenizer: {tokenizer_id}")
for s in subjects:
    d = load_dataset("HAERAE-HUB/KMMLU", s)
    d.save_to_disk(str(out / f"kmmlu-{s.lower()}"))
    print(f"{s}: " + ", ".join(f"{k}={len(v)}" for k, v in d.items()))
