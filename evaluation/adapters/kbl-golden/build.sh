#!/usr/bin/env bash
# adapter 이미지 빌드/푸시.   IMAGE=quay.io/<org>/kbl-golden-adapter TAG=v1 ./build.sh [--push]
# 폐쇄망: 사내 레지스트리로 IMAGE 를 바꾸고, provider ConfigMap(provider-kbl-golden.yaml)의 image 도 맞춘다.
set -euo pipefail
cd "$(dirname "$0")"
IMAGE=${IMAGE:-quay.io/gunoh/kbl-golden-adapter}
TAG=${TAG:-v1}
podman build --platform linux/amd64 -t "$IMAGE:$TAG" -f Containerfile .
if [[ ${1:-} == --push ]]; then
  podman push "$IMAGE:$TAG"
  echo "pushed: $IMAGE:$TAG  digest: $(podman image inspect "$IMAGE:$TAG" --format '{{.Digest}}')"
fi
