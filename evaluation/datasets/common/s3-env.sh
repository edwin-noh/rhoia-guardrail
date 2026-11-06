# source 해서 사용: rustfs 접속 정보를 현재 셸에 설정한다 (oc 로그인 필요)
# 폐쇄망/다른 S3 를 쓰면 아래 값을 직접 export 한다.
export AWS_ACCESS_KEY_ID=$(oc get secret rustfs-credentials -n rustfs -o jsonpath='{.data.AWS_ACCESS_KEY_ID}' | base64 -d)
export AWS_SECRET_ACCESS_KEY=$(oc get secret rustfs-credentials -n rustfs -o jsonpath='{.data.AWS_SECRET_ACCESS_KEY}' | base64 -d)
export AWS_S3_ENDPOINT=${AWS_S3_ENDPOINT:-https://$(oc get route rustfs-api -n rustfs -o jsonpath='{.spec.host}')}
export AWS_DEFAULT_REGION=${AWS_DEFAULT_REGION:-us-east-1}
export S3_VERIFY_SSL=${S3_VERIFY_SSL:-false}   # 사설 인증서 lab 환경
export S3_BUCKET=${S3_BUCKET:-kbl-eval}
