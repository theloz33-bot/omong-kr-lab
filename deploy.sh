#!/usr/bin/env bash
# 빌드 → 감사 → 커밋 → 푸시. GitHub Pages(main /docs)가 1분 안에 다시 배포합니다.
#   ./deploy.sh "글 추가: 겨울 습도와 가습기 기준"
set -euo pipefail
cd "$(dirname "$0")"

python3 build.py --check
python3 tools/audit.py

git add -A
if git diff --cached --quiet; then
  echo "커밋할 변경이 없습니다 — docs/ 가 이미 최신입니다"
  exit 0
fi
git commit -m "${1:-글 업데이트}"
git push origin HEAD:main
echo "푸시 완료. 1분 뒤 확인: https://theloz33-bot.github.io/omong-kr-lab/"
