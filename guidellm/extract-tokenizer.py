#!/usr/bin/env python3
"""modelcar 이미지에서 tokenizer 파일만 꺼낸다 (가중치 레이어는 받지 않는다).

olot / hf-modelcar 로 만든 modelcar 는 파일 하나가 레이어 하나이고, 레이어 annotation
`olot.layer.content.inlayerpath` 에 /models/<파일> 경로가 있다. 그 annotation 으로 tokenizer 레이어만 받는다.
annotation 이 없는 이미지(레이어 하나에 모델 전체가 든 경우)는 oc image extract 를 쓴다 (docs/04 참고).

표준 라이브러리만 쓴다. 레지스트리 인증은 podman/skopeo 의 auth.json 을 읽는다.

    python3 extract-tokenizer.py <registry>/<repo>@sha256:<digest> <출력 디렉터리>
      [--authfile ~/.config/containers/auth.json] [--ca-file <사내 CA>] [--insecure]
"""
from __future__ import annotations

import argparse
import base64
import gzip
import io
import json
import os
import re
import ssl
import sys
import tarfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

# AutoTokenizer.from_pretrained(<dir>) 에 필요한 파일 (없는 것은 건너뛴다)
TOKENIZER_FILES = {
    "tokenizer.json", "tokenizer_config.json", "special_tokens_map.json", "added_tokens.json",
    "vocab.json", "merges.txt", "tokenizer.model", "vocab.txt", "chat_template.jinja",
    "config.json", "generation_config.json",
}
ACCEPT = ", ".join([
    "application/vnd.oci.image.index.v1+json", "application/vnd.oci.image.manifest.v1+json",
    "application/vnd.docker.distribution.manifest.list.v2+json",
    "application/vnd.docker.distribution.manifest.v2+json",
])


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):   # 리다이렉트(CDN)는 직접 따라가며 Authorization 을 뺀다
        return None


class Registry:
    def __init__(self, registry: str, repo: str, authfile: str | None, ctx: ssl.SSLContext):
        self.base, self.repo, self.ctx = f"https://{registry}", repo, ctx
        self.basic = self._basic_from_authfile(registry, authfile)
        self.auth_header: str | None = None
        self.opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=ctx), NoRedirect)

    @staticmethod
    def _basic_from_authfile(registry: str, authfile: str | None) -> str | None:
        paths = [authfile] if authfile else [os.environ.get("REGISTRY_AUTH_FILE"),
                                             os.path.expanduser("~/.config/containers/auth.json"),
                                             f"/run/user/{os.getuid()}/containers/auth.json",
                                             os.path.expanduser("~/.docker/config.json")]
        for p in filter(None, paths):
            try:
                auths = json.loads(Path(p).read_text()).get("auths", {})
            except (OSError, json.JSONDecodeError):
                continue
            for key, val in auths.items():
                if key.removeprefix("https://").split("/")[0] == registry and val.get("auth"):
                    return val["auth"]
        return None

    def _token(self, challenge: str) -> None:
        """WWW-Authenticate 에 맞춰 Bearer 토큰을 받거나 Basic 을 쓴다."""
        if challenge.lower().startswith("basic"):
            if not self.basic:
                sys.exit("레지스트리가 Basic 인증을 요구합니다. --authfile 을 지정하세요 (podman login 후 auth.json)")
            self.auth_header = f"Basic {self.basic}"
            return
        params = dict(re.findall(r'(\w+)="([^"]*)"', challenge))
        query = {k: v for k, v in params.items() if k in ("service", "scope")}
        req = urllib.request.Request(params["realm"] + "?" + urllib.parse.urlencode(query))
        if self.basic:
            req.add_header("Authorization", f"Basic {self.basic}")
        with urllib.request.urlopen(req, context=self.ctx, timeout=60) as r:
            body = json.loads(r.read())
        self.auth_header = f"Bearer {body.get('token') or body.get('access_token')}"

    def get(self, path: str, accept: str | None = None) -> bytes:
        url = f"{self.base}/v2/{self.repo}/{path}"
        for _ in range(3):
            req = urllib.request.Request(url)
            if accept:
                req.add_header("Accept", accept)
            if self.auth_header and url.startswith(self.base):
                req.add_header("Authorization", self.auth_header)
            try:
                with self.opener.open(req, timeout=300) as r:
                    return r.read()
            except urllib.error.HTTPError as e:
                if e.code == 401 and url.startswith(self.base) and not self.auth_header:
                    self._token(e.headers.get("WWW-Authenticate", ""))
                    continue
                if e.code in (301, 302, 303, 307, 308):
                    url = e.headers["Location"]          # blob 저장소(CDN)로 이동. 인증 헤더 없이 받는다
                    continue
                raise SystemExit(f"GET {url} → {e.code} {e.read()[:200]!r}")
        raise SystemExit(f"GET {url} 실패")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("image", help="<registry>/<repo>@sha256:<digest> 또는 :<tag>")
    ap.add_argument("outdir")
    ap.add_argument("--authfile")
    ap.add_argument("--ca-file")
    ap.add_argument("--insecure", action="store_true", help="TLS 검증 생략 (lab 용)")
    a = ap.parse_args()

    m = re.match(r"([^/]+)/(.+?)(?:@(sha256:[0-9a-f]+)|:([\w.-]+))$", a.image)
    if not m:
        sys.exit("이미지 형식: <registry>/<repo>@sha256:<digest>")
    registry, repo, digest, tag = m.groups()
    ctx = ssl._create_unverified_context() if a.insecure else ssl.create_default_context(cafile=a.ca_file)
    reg = Registry(registry, repo, a.authfile, ctx)

    manifest = json.loads(reg.get(f"manifests/{digest or tag}", ACCEPT))
    if "manifests" in manifest:                       # index 면 linux/amd64 manifest 를 고른다
        pick = next((d for d in manifest["manifests"]
                     if d.get("platform", {}).get("architecture") in ("amd64", None)), manifest["manifests"][0])
        manifest = json.loads(reg.get(f"manifests/{pick['digest']}", ACCEPT))

    layers = []
    for layer in manifest.get("layers", []):
        path = (layer.get("annotations") or {}).get("olot.layer.content.inlayerpath", "")
        if path.startswith("/models/") and Path(path).name in TOKENIZER_FILES:
            layers.append((path, layer))
    if not layers:
        sys.exit("tokenizer 레이어 annotation 이 없습니다 (olot 형식이 아님). oc image extract 를 쓰세요.")

    out = Path(a.outdir)
    out.mkdir(parents=True, exist_ok=True)
    for path, layer in layers:
        blob = reg.get(f"blobs/{layer['digest']}")
        if blob[:2] == b"\x1f\x8b":
            blob = gzip.decompress(blob)
        name = Path(path).name
        with tarfile.open(fileobj=io.BytesIO(blob)) as tar:
            member = next((t for t in tar.getmembers() if t.isfile() and Path(t.name).name == name), None)
            if member is None:
                print(f"  {name}: 레이어 안에 파일이 없음, 건너뜀")
                continue
            (out / name).write_bytes(tar.extractfile(member).read())
        print(f"  {name:<24} {(out / name).stat().st_size:>10,} bytes")
    print(f"완료: {out} (가중치 레이어는 받지 않음)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
