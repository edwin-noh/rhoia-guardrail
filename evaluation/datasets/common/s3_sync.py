#!/usr/bin/env python3
"""로컬 디렉터리를 S3(rustfs/MinIO/AWS) 경로에 업로드한다. 버킷이 없으면 만든다.

    python3 s3_sync.py <local_dir> s3://<bucket>/<prefix>

자격증명/엔드포인트는 환경변수: AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_S3_ENDPOINT,
AWS_DEFAULT_REGION(기본 us-east-1), S3_VERIFY_SSL(기본 true, 사설 인증서면 false 또는 CA 경로)
EvalHub 는 지정한 prefix 아래 객체를 상대 경로 그대로 Job pod 의 /test_data 에 내려받는다.
"""
import os
import sys
from pathlib import Path

import boto3
from botocore.exceptions import ClientError


def main() -> int:
    if len(sys.argv) != 3 or not sys.argv[2].startswith("s3://"):
        print(__doc__)
        return 2
    local = Path(sys.argv[1])
    bucket, _, prefix = sys.argv[2][5:].partition("/")
    prefix = prefix.strip("/")
    verify = os.environ.get("S3_VERIFY_SSL", "true")
    verify = {"true": True, "false": False}.get(verify.lower(), verify)
    if verify is False:
        import urllib3

        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    s3 = boto3.client(
        "s3",
        endpoint_url=os.environ.get("AWS_S3_ENDPOINT"),
        region_name=os.environ.get("AWS_DEFAULT_REGION", "us-east-1"),
        verify=verify,
    )
    try:
        s3.head_bucket(Bucket=bucket)
    except ClientError:
        s3.create_bucket(Bucket=bucket)
        print(f"버킷 생성: {bucket}")

    # 같은 prefix 의 기존 객체를 지워 이전 버전 파일이 섞이지 않게 한다
    old = s3.list_objects_v2(Bucket=bucket, Prefix=prefix + "/").get("Contents", [])
    for obj in old:
        s3.delete_object(Bucket=bucket, Key=obj["Key"])

    files = [p for p in local.rglob("*") if p.is_file() and not p.name.endswith(".lock")]
    for p in files:
        key = f"{prefix}/{p.relative_to(local).as_posix()}"
        s3.upload_file(str(p), bucket, key)
    total = sum(p.stat().st_size for p in files)
    print(f"업로드 완료: {len(files)}개 파일, {total / 1e6:.1f}MB → s3://{bucket}/{prefix}/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
