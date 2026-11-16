#!/usr/bin/env python3
"""docs/ 발행 전 자동 감사.

점검 순서
  1. 내부 링크가 모두 docs/ 안의 실제 파일로 연결되는지
  2. XML 파일(sitemap·rss)이 파싱되는지
  3. 모든 HTML의 태그 균형과 h1 개수
  4. 개인 식별 정보(실명·전화·개인 메일·주소)가 공개 산출물에 없는지
  5. 쿠팡 링크가 있는 글에 파트너스 고지 문구와 rel="sponsored"가 있는지
  6. 크몽 링크가 있는 글에 서비스 링크와 제휴 표시가 있는지

실행: python3 tools/audit.py      (하나라도 걸리면 종료 코드 1)
"""

from __future__ import annotations

import json
import re
import sys
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
DATA = ROOT / "data"

VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link",
        "meta", "param", "source", "track", "wbr"}

# 개인정보 검사 패턴
#   정확한 문자열 목록은 저장소에 올리지 않는다. 작업 환경의 .audit_patterns.json(gitignore)에
#   두고 읽어 쓰며, 그 파일이 없는 곳에서도 이메일·휴대폰 같은 일반 패턴은 항상 검사한다.
PATTERN_FILE = ROOT / ".audit_patterns.json"
GENERIC = [
    r"[A-Za-z0-9._%+-]+@(?:gmail|naver|daum|hanmail|kakao|nate)\.com",
    r"01\d[-\s]?\d{3,4}[-\s]?\d{4}",
]


def load_patterns() -> list[str]:
    patterns = list(GENERIC)
    if PATTERN_FILE.exists():
        try:
            data = json.loads(PATTERN_FILE.read_text(encoding="utf-8"))
            patterns = list(data.get("patterns", [])) + patterns
        except json.JSONDecodeError as exc:
            print(f"  (.audit_patterns.json 형식 오류: {exc})")
    else:
        print("  참고: .audit_patterns.json 이 없어 일반 패턴만 검사합니다")
    return patterns


PERSONAL = load_patterns()

failures: list[str] = []


class Balance(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[str] = []
        self.errors: list[str] = []
        self.h1 = 0

    def handle_starttag(self, tag, attrs):
        if tag == "h1":
            self.h1 += 1
        if tag not in VOID:
            self.stack.append(tag)

    def handle_endtag(self, tag):
        if tag in VOID:
            return
        if not self.stack:
            self.errors.append(f"stray </{tag}>")
        elif self.stack[-1] == tag:
            self.stack.pop()
        elif tag in self.stack:
            while self.stack and self.stack[-1] != tag:
                self.errors.append(f"unclosed <{self.stack.pop()}>")
            if self.stack:
                self.stack.pop()
        else:
            self.errors.append(f"unmatched </{tag}>")


def resolve(href: str, page: Path, base_path: str) -> Path | None:
    if href.startswith(("#", "mailto:", "tel:", "javascript:", "data:")):
        return None
    parsed = urlparse(href)
    if parsed.scheme in ("http", "https"):
        if not href.startswith("https://theloz33-bot.github.io"):
            return None
        path = parsed.path
    else:
        path = parsed.path
        if not path:
            return None
        if not path.startswith("/"):
            target = (page.parent / path).resolve()
            if path.endswith("/") or target.is_dir():
                target = target / "index.html"
            return target

    if not path.startswith(base_path):
        return Path("/outside-site-base")
    rel = path[len(base_path):]
    target = (DOCS / rel).resolve()
    if rel == "" or rel.endswith("/") or target.is_dir():
        target = target / "index.html"
    return target


def main() -> int:
    if not DOCS.exists():
        sys.exit("docs/ 가 없습니다 — python3 build.py 를 먼저 실행하세요")

    site = json.loads((DATA / "site.json").read_text(encoding="utf-8"))
    coupang = json.loads((DATA / "coupang.json").read_text(encoding="utf-8"))
    kmong = json.loads((DATA / "kmong.json").read_text(encoding="utf-8"))
    base_path = site["base_path"]
    disclosure = coupang["disclosure"]

    pages = sorted(DOCS.rglob("*.html"))
    if not pages:
        failures.append("HTML 산출물이 없습니다")

    if len(list((DOCS / "posts").glob("*/index.html"))) < 3:
        failures.append("발행된 글이 3편 미만입니다")

    for page in pages:
        text = page.read_text(encoding="utf-8")

        for href in re.findall(r'(?:href|src)="([^"]+)"', text):
            target = resolve(href, page, base_path)
            if target is None:
                continue
            if not target.exists():
                failures.append(f"{page.relative_to(DOCS)}: 깨진 링크 {href}")

        if re.search(r'(?:href|src)="http://', text):
            failures.append(f"{page.relative_to(DOCS)}: https 가 아닌 링크")

        for block in re.findall(r'<script type="application/ld\+json">(.*?)</script>', text, re.S):
            try:
                json.loads(block)
            except json.JSONDecodeError as exc:
                failures.append(f"{page.relative_to(DOCS)}: JSON-LD 오류 ({exc})")

        parser = Balance()
        parser.feed(text)
        if parser.errors or parser.stack:
            failures.append(
                f"{page.relative_to(DOCS)}: 태그 불균형 {parser.errors[:3]} open={parser.stack[:3]}"
            )
        if parser.h1 != 1:
            failures.append(f"{page.relative_to(DOCS)}: h1 이 {parser.h1}개")

    for xml_file in sorted(DOCS.glob("*.xml")):
        try:
            ET.parse(xml_file)
        except ET.ParseError as exc:
            failures.append(f"{xml_file.name}: XML 파싱 오류 ({exc})")

    # 개인 식별 정보
    for path in sorted(DOCS.rglob("*")):
        if not path.is_file() or path.suffix not in {".html", ".xml", ".css", ".txt", ".svg", ""}:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        stripped = text.replace("theloz33-bot.github.io", "").replace("theloz33-bot", "")
        for pattern in PERSONAL:
            for match in re.finditer(pattern, stripped):
                line = stripped[max(0, match.start() - 40): match.end() + 40].replace("\n", " ")
                failures.append(
                    f"{path.relative_to(DOCS)}: 개인정보 의심 '{match.group(0)}' …{line}…"
                )

    # 쿠팡·크몽 규정
    for post in sorted((DOCS / "posts").glob("*/index.html")):
        text = post.read_text(encoding="utf-8")
        slug = post.parent.name
        has_coupang = "link.coupang.com" in text
        if "<table" not in text and "checklist" not in text:
            failures.append(f"{slug}: 표나 체크리스트가 없습니다")
        if has_coupang:
            if disclosure not in text:
                failures.append(f"{slug}: 쿠팡 파트너스 고지 문구 누락")
            if 'rel="sponsored nofollow noopener"' not in text:
                failures.append(f"{slug}: 쿠팡 링크에 rel=sponsored 누락")
            if coupang["store"]["url"] not in text:
                failures.append(f"{slug}: 스토어 오몽 링크 누락")
        if "kmong.com/gig/" in text:
            if 'rel="sponsored nofollow noopener"' not in text:
                failures.append(f"{slug}: 크몽 링크에 rel=sponsored 누락")
            if kmong["seller"]["note"] not in text:
                failures.append(f"{slug}: 크몽 가격·수수료 안내 누락")

    # 필수 파일
    for name in ("sitemap.xml", "rss.xml", "robots.txt", "404.html", ".nojekyll",
                 "about/index.html", "disclosure/index.html"):
        if not (DOCS / name).exists():
            failures.append(f"필수 산출물 누락: {name}")

    if failures:
        print("AUDIT FAILED")
        for item in failures:
            print("  -", item)
        return 1

    print(f"AUDIT OK — {len(pages)}개 페이지, XML {len(list(DOCS.glob('*.xml')))}개, "
          f"개인정보 0건, 쿠팡·크몽 고지 확인")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
