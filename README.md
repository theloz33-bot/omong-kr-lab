# 오몽 라이프 & AI 연구소 (omong-kr-lab)

생활 정보와 AI 제작을 다루는 한국어 정적 매거진 블로그입니다. 의존성 없이 파이썬 표준 라이브러리만으로 빌드합니다.
라이브: https://theloz33-bot.github.io/omong-kr-lab/

## 구조

```
build.py            docs/ 생성기 (표준 라이브러리만)
data/site.json      브랜드·주소·메뉴·카테고리
data/coupang.json   쿠팡 파트너스 상품 링크·이미지·고지 문구 (AF2611856)
data/kmong.json     크몽 '오몽이' 서비스 패키지
posts/*.md          글 3편 + _template.md
pages/*.md          소개 / 제휴·개인정보
templates/base.html 페이지 틀
static/             CSS·파비콘 → docs/assets/
tools/audit.py      발행 전 감사
deploy.sh           빌드·감사·커밋·푸시
docs/               빌드 산출물 (GitHub Pages main /docs)
```

## 개인정보 검사 패턴

정확한 검사 문자열은 저장소에 올리지 않습니다. 작업 환경 루트의 `.audit_patterns.json`(gitignore 대상)에 두고 `tools/audit.py`가 읽어 씁니다. 그 파일이 없는 곳에서도 이메일·휴대폰 같은 일반 패턴은 항상 검사합니다.

```json
{ "patterns": ["검사할 문자열", "정규식도 가능"] }
```

## 매일 발행 절차

```bash
cd blog-omong-kr
cp posts/_template.md posts/2026-11-17-새글.md   # front matter 채우기
python3 build.py --check                        # 본문 1,200~2,000자 검사
python3 tools/audit.py                          # 링크·XML·태그·개인정보 감사
./deploy.sh "글 추가: 제목"
```

## 글쓰기 규칙

- 본문은 공백 제외 1,200~2,000자. 표나 체크리스트를 최소 하나 넣습니다.
- 쿠팡 링크가 들어가는 글에는 파트너스 고지 문구가 자동으로 붙습니다. 문구를 지우지 마세요.
- 크몽 서비스는 텍스트에서 "이 블로그를 운영하는 쪽이 판매한다"고 밝힙니다.
- 개인정보(실명·전화·주소·개인 메일)는 코드·HTML·커밋 어디에도 넣지 않습니다. `audit.py`가 검사합니다.
- 브랜드 표기는 `오몽 라이프 & AI 연구소` 하나만 씁니다.

## 커밋 신원

`git config`는 로컬에서 `오몽 라이프 & AI 연구소 <theloz33-bot@users.noreply.github.com>`로 고정되어 있어 개인 메일이 커밋에 남지 않습니다.
