#!/usr/bin/env python3
"""오몽 라이프 & AI 연구소 — 정적 매거진 블로그 빌더.

표준 라이브러리만 씁니다. 외부 CDN·폰트·스크립트를 쓰지 않습니다.

    python3 build.py            # posts/ + data/ 로 docs/ 재생성
    python3 build.py --check    # 본문 1,200~2,000자(공백 제외) 검사

입력
    data/site.json     브랜드·URL·메뉴·카테고리
    data/coupang.json  쿠팡 파트너스 상품 링크·이미지·고지 문구
    data/kmong.json    크몽 '오몽이' 서비스 패키지
    data/cpa.json      제휴(CPA) 상담 캠페인 링크·고지 문구
    posts/*.md         글 (front matter + 본문)
    templates/*.html   페이지 틀 ({{placeholder}} 치환)
    static/            docs/assets/ 로 그대로 복사
    pages/*.md         소개·제휴 고지 같은 고정 페이지

출력
    docs/              홈, 글, 카테고리, sitemap.xml, rss.xml, robots.txt, 404.html
"""

from __future__ import annotations

import html
import json
import re
import shutil
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
POSTS = ROOT / "posts"
TEMPLATES = ROOT / "templates"
STATIC = ROOT / "static"
OUT = ROOT / "docs"

CHAR_MIN, CHAR_MAX = 1200, 2000
KST = timezone(timedelta(hours=9))


# ---------------------------------------------------------------- 유틸

def load_json(path: Path) -> dict:
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


def slugify(value: str) -> str:
    value = re.sub(r"[^\w\s-]", "", value.lower(), flags=re.UNICODE)
    value = re.sub(r"[\s_]+", "-", value.strip())
    return re.sub(r"-{2,}", "-", value).strip("-")


def read_template(name: str) -> str:
    return (TEMPLATES / name).read_text(encoding="utf-8")


def render(template: str, values: dict) -> str:
    out = template
    for key, value in values.items():
        out = out.replace("{{" + key + "}}", str(value))
    leftovers = re.findall(r"\{\{[a-z_]+\}\}", out)
    if leftovers:
        raise SystemExit(f"템플릿 자리표시자가 남았습니다: {sorted(set(leftovers))}")
    return out


# ---------------------------------------------------------------- 마크다운

TOKEN = "\x00{}\x00"


def escape(text: str) -> str:
    return html.escape(text, quote=False)


def inline(text: str, ctx: dict) -> str:
    """인라인 마크다운을 변환. 먼저 만든 조각은 토큰으로 감춰 두고 마지막에 되돌립니다."""
    stash: list[str] = []

    def keep(fragment: str) -> str:
        stash.append(fragment)
        return TOKEN.format(len(stash) - 1)

    text = re.sub(
        r"`([^`]+)`",
        lambda m: keep("<code>" + html.escape(m.group(1)) + "</code>"),
        text,
    )

    text = escape(text)

    text = re.sub(
        r"!\[([^\]]*)\]\(([^)\s]+)\)",
        lambda m: keep(
            f'<img src="{m.group(2)}" alt="{m.group(1)}" loading="lazy" '
            'decoding="async" referrerpolicy="no-referrer">'
        ),
        text,
    )
    text = re.sub(
        r"\[([^\]]+)\]\(([^)\s]+)\)",
        lambda m: keep(anchor(m.group(2), m.group(1), ctx)),
        text,
    )

    # [[cp:상품키|링크문구]] 쿠팡 파트너스 링크
    def coupang_link(m: re.Match) -> str:
        key, label = m.group(1), m.group(2)
        product = ctx["coupang"]["products"].get(key)
        if not product:
            raise SystemExit(f"data/coupang.json 에 없는 상품 키: {key}")
        return keep(
            f'<a href="{product["url"]}" rel="sponsored nofollow noopener" '
            f'class="inline-shop">{label}</a>'
        )

    text = re.sub(r"\[\[cp:([a-z0-9-]+)\|([^\]]+)\]\]", coupang_link, text)

    # [[kmong:서비스키|링크문구]] 크몽 서비스 링크
    def kmong_link(m: re.Match) -> str:
        key, label = m.group(1), m.group(2)
        service = ctx["kmong"]["services"].get(key)
        if not service:
            raise SystemExit(f"data/kmong.json 에 없는 서비스 키: {key}")
        return keep(
            f'<a href="{service["url"]}" rel="sponsored nofollow noopener" '
            f'class="inline-shop">{label}</a>'
        )

    text = re.sub(r"\[\[kmong:([a-z0-9-]+)\|([^\]]+)\]\]", kmong_link, text)

    # [[cpa:캠페인키|링크문구]] 제휴 상담 링크. 링크가 아직 없으면 문구만 굵게 남깁니다.
    def cpa_link(m: re.Match) -> str:
        key, label = m.group(1), m.group(2)
        campaign = ctx["cpa"]["campaigns"].get(key)
        if not campaign:
            raise SystemExit(f"data/cpa.json 에 없는 캠페인 키: {key}")
        url = campaign.get("url", "")
        if not url:
            return keep(f'<strong>{label}</strong>')
        return keep(
            f'<a href="{url}" rel="sponsored nofollow noopener" '
            f'class="inline-shop">{label}</a>'
        )

    text = re.sub(r"\[\[cpa:([a-z0-9-]+)\|([^\]]+)\]\]", cpa_link, text)

    text = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"(?<![\w*])\*([^*\n]+)\*(?![\w*])", r"<em>\1</em>", text)

    for index, fragment in enumerate(stash):
        text = text.replace(TOKEN.format(index), fragment)
    return text


def anchor(href: str, label: str, ctx: dict) -> str:
    base = ctx["site"]["base_url"].rstrip("/")
    attrs = [f'href="{href}"']
    if href.startswith("http") and not href.startswith(base):
        rel = "noopener"
        if any(host in href for host in ("coupang.com", "kmong.com")):
            rel = "sponsored nofollow noopener"
        attrs.append(f'rel="{rel}"')
    return f"<a {' '.join(attrs)}>{label}</a>"


def md_to_html(body: str, ctx: dict) -> tuple[str, list[dict]]:
    lines = body.replace("\r\n", "\n").split("\n")
    blocks: list[str] = []
    toc: list[dict] = []
    used_ids: set[str] = set()
    i = 0

    def heading_id(text: str) -> str:
        base = slugify(re.sub(r"[*`\[\]]", "", text)) or "section"
        candidate, n = base, 2
        while candidate in used_ids:
            candidate = f"{base}-{n}"
            n += 1
        used_ids.add(candidate)
        return candidate

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        if not stripped:
            i += 1
            continue

        # 코드 블록
        if stripped.startswith("```"):
            lang = stripped[3:].strip()
            i += 1
            code: list[str] = []
            while i < len(lines) and not lines[i].strip().startswith("```"):
                code.append(lines[i])
                i += 1
            i += 1
            cls = f' class="language-{escape(lang)}"' if lang else ""
            blocks.append(
                f"<pre><code{cls}>" + html.escape("\n".join(code)) + "</code></pre>"
            )
            continue

        # ::: tip 제목 … ::: 박스
        if stripped.startswith(":::"):
            rest = stripped[3:].strip()
            kind, _, title = rest.partition(" ")
            kind = kind or "tip"
            title = title.strip()
            i += 1
            inner: list[str] = []
            while i < len(lines) and not lines[i].strip().startswith(":::"):
                inner.append(lines[i])
                i += 1
            i += 1
            paragraphs: list[str] = []
            chunk: list[str] = []
            for row in inner:
                if row.strip():
                    chunk.append(row.strip())
                elif chunk:
                    paragraphs.append(" ".join(chunk))
                    chunk = []
            if chunk:
                paragraphs.append(" ".join(chunk))
            head = f'<p class="callout-title">{inline(title, ctx)}</p>' if title else ""
            body_html = "".join(f"<p>{inline(p, ctx)}</p>" for p in paragraphs)
            blocks.append(f'<div class="callout callout-{escape(kind)}">{head}{body_html}</div>')
            continue

        # 제목
        match = re.match(r"^(#{2,4})\s+(.*)$", stripped)
        if match:
            level = len(match.group(1))
            label = match.group(2).strip()
            hid = heading_id(label)
            blocks.append(f'<h{level} id="{hid}">{inline(label, ctx)}</h{level}>')
            if level in (2, 3):
                toc.append(
                    {
                        "id": hid,
                        "label": re.sub(r"[*`]", "", label),
                        "level": level,
                    }
                )
            i += 1
            continue

        # 구분선
        if re.match(r"^-{3,}$", stripped):
            blocks.append("<hr>")
            i += 1
            continue

        # 표
        if stripped.startswith("|") and i + 1 < len(lines) and re.match(
            r"^\|[\s:|-]+\|$", lines[i + 1].strip()
        ):
            header = [c.strip() for c in stripped.strip("|").split("|")]
            i += 2
            rows: list[list[str]] = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                rows.append([c.strip() for c in lines[i].strip().strip("|").split("|")])
                i += 1
            head = "".join(f"<th>{inline(c, ctx)}</th>" for c in header)
            body_rows = "".join(
                "<tr>"
                + "".join(f"<td>{inline(c, ctx)}</td>" for c in row)
                + "</tr>"
                for row in rows
            )
            blocks.append(
                '<div class="table-wrap"><table><thead><tr>'
                + head
                + "</tr></thead><tbody>"
                + body_rows
                + "</tbody></table></div>"
            )
            continue

        # 인용
        if stripped.startswith(">"):
            quote: list[str] = []
            while i < len(lines) and lines[i].strip().startswith(">"):
                quote.append(lines[i].strip()[1:].strip())
                i += 1
            inner = " ".join(q for q in quote if q)
            blocks.append(f"<blockquote><p>{inline(inner, ctx)}</p></blockquote>")
            continue

        # 목록
        if re.match(r"^([-*]|\d+\.)\s+", stripped):
            ordered = bool(re.match(r"^\d+\.\s+", stripped))
            items: list[tuple[str, str]] = []
            while i < len(lines) and re.match(r"^([-*]|\d+\.)\s+", lines[i].strip()):
                raw = re.sub(r"^([-*]|\d+\.)\s+", "", lines[i].strip())
                checkbox = None
                box = re.match(r"^\[( |x|X)\]\s+(.*)$", raw)
                if box:
                    checkbox = "checked" if box.group(1).lower() == "x" else ""
                    raw = box.group(2)
                i += 1
                while (
                    i < len(lines)
                    and lines[i].startswith("  ")
                    and lines[i].strip()
                    and not re.match(r"^([-*]|\d+\.)\s+", lines[i].strip())
                ):
                    raw += " " + lines[i].strip()
                    i += 1
                items.append((checkbox, inline(raw, ctx)))
            tag = "ol" if ordered else "ul"
            if not ordered and any(cb is not None for cb, _ in items):
                cls = ' class="checklist"'
            else:
                cls = ""
            rendered = ""
            for checkbox, content in items:
                if ordered:
                    rendered += f"<li>{content}</li>"
                elif checkbox == "checked":
                    rendered += f'<li class="done">{content}</li>'
                elif checkbox is not None:
                    rendered += f'<li class="todo">{content}</li>'
                else:
                    rendered += f"<li>{content}</li>"
            blocks.append(f"<{tag}{cls}>{rendered}</{tag}>")
            continue

        # 문단
        para: list[str] = []
        while i < len(lines) and lines[i].strip() and not re.match(
            r"^(#{2,4}\s|```|:::|>|\||[-*]\s|\d+\.\s|-{3,}$)", lines[i].strip()
        ):
            para.append(lines[i].strip())
            i += 1
        merged = " ".join(para)
        cls = ' class="lede"' if not blocks else ""
        blocks.append(f"<p{cls}>{inline(merged, ctx)}</p>")

    return "\n".join(blocks), toc


def parse_front_matter(text: str) -> tuple[dict, str]:
    if not text.startswith("---"):
        return {}, text
    parts = text.split("\n")
    meta: dict = {}
    end = None
    for index in range(1, len(parts)):
        line = parts[index].rstrip()
        if line.strip() in ("---", "..."):
            end = index
            break
        if not line.strip() or line.strip().startswith("#"):
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip()
        if value.startswith("[") and value.endswith("]"):
            meta[key] = [v.strip().strip("\"'") for v in value[1:-1].split(",") if v.strip()]
        elif value:
            meta[key] = value.strip("\"'")
        else:
            meta[key] = ""
    if end is None:
        raise SystemExit("front matter 가 --- 로 열렸는데 닫히지 않았습니다")
    return meta, "\n".join(parts[end + 1:])


def char_count(text: str) -> int:
    """공백과 마크다운 기호를 뺀 실제 글자 수(한글·영문·숫자 기준)."""
    text = re.sub(r"```.*?```", " ", text, flags=re.S)
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", text)
    text = re.sub(r"\[\[(?:cp|kmong|cpa):[a-z0-9-]+\|([^\]]+)\]\]", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"^\s*\|?[\s:|-]+\|?\s*$", " ", text, flags=re.M)
    text = re.sub(r"[#*`>|\-\[\]():]", "", text)
    return len(re.sub(r"\s+", "", text))


# ---------------------------------------------------------------- 조각

def load_posts(ctx: dict) -> list[dict]:
    site = ctx["site"]
    categories = {c["key"]: c for c in site["categories"]}
    posts: list[dict] = []
    for path in sorted(POSTS.glob("*.md")):
        if path.name.startswith("_"):
            continue
        meta, body = parse_front_matter(path.read_text(encoding="utf-8"))
        missing = [k for k in ("title", "slug", "date", "category", "description") if not meta.get(k)]
        if missing:
            raise SystemExit(f"{path.name}: front matter 누락 {missing}")
        if meta["category"] not in categories:
            raise SystemExit(f"{path.name}: site.json 에 없는 카테고리 '{meta['category']}'")
        if re.search(r"\]\(\[\[(?:cp|kmong|cpa):", body):
            raise SystemExit(
                f"{path.name}: 링크 문법 안에 [[cp:…]]/[[kmong:…]]/[[cpa:…]] 를 겹쳐 쓸 수 없습니다 — "
                "[[cp:키|문구]] 형태만 쓰세요"
            )
        body_html, toc = md_to_html(body, ctx)
        chars = char_count(body)
        posts.append(
            {
                "path": path,
                "meta": meta,
                "title": meta["title"],
                "slug": meta["slug"],
                "date": meta["date"],
                "category": meta["category"],
                "category_label": categories[meta["category"]]["label"],
                "description": meta["description"],
                "excerpt": meta.get("excerpt", meta["description"]),
                "tags": meta.get("tags", []),
                "coupang": meta.get("coupang", []),
                "kmong": meta.get("kmong", []),
                "cpa": meta.get("cpa", []),
                "store": str(meta.get("store", "")).lower() in ("true", "yes", "1"),
                "featured": str(meta.get("featured", "")).lower() in ("true", "yes", "1"),
                "html": body_html,
                "toc": toc,
                "chars": chars,
                "reading": max(1, round(chars / 450)),
            }
        )
    posts.sort(key=lambda p: p["date"], reverse=True)
    return posts


def fmt_date(value: str, short: bool = False) -> str:
    date = datetime.strptime(value, "%Y-%m-%d")
    if short:
        return f"{date.year}.{date.month:02d}.{date.day:02d}"
    return f"{date.year}년 {date.month}월 {date.day}일"


def rfc822(value: str) -> str:
    date = datetime.strptime(value, "%Y-%m-%d").replace(hour=9, tzinfo=KST)
    days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
              "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    return (
        f"{days[date.weekday()]}, {date.day:02d} {months[date.month - 1]} {date.year} "
        f"{date.hour:02d}:{date.minute:02d}:00 +0900"
    )


def nav_html(ctx: dict, current: str) -> str:
    site = ctx["site"]
    items = []
    for item in site["nav"]:
        active = ' aria-current="page"' if item["key"] == current else ""
        items.append(f'<a href="{site["base_path"]}{item["path"]}"{active}>{item["label"]}</a>')
    return "".join(items)


def format_price(value: str) -> str:
    match = re.match(r"^([\d,]+)원$", value)
    if not match:
        return value
    return f"{int(match.group(1).replace(',', '')):,}원"


# ---------------------------------------------------------------- 상품·의뢰 박스

def coupang_box(post: dict, ctx: dict) -> str:
    keys = post["coupang"]
    if not keys:
        return ""
    data = ctx["coupang"]
    cards = []
    for key in keys:
        product = data["products"].get(key)
        if not product:
            raise SystemExit(f"{post['slug']}: data/coupang.json 에 없는 상품 키 {key}")
        cards.append(
            f"""<li class="shop-card">
  <a class="shop-media" href="{product['url']}" rel="sponsored nofollow noopener" tabindex="-1" aria-hidden="true">
    <img src="{product['image']}" alt="" loading="lazy" decoding="async" referrerpolicy="no-referrer">
  </a>
  <div class="shop-body">
    <span class="shop-badge">{product.get('badge', '추천')}</span>
    <a class="shop-name" href="{product['url']}" rel="sponsored nofollow noopener">{product['name']}</a>
    <p class="shop-price"><strong>{format_price(product['price'])}</strong>
      <s>{format_price(product.get('was', ''))}</s>
      <em>{product.get('off', '')}</em></p>
    <p class="shop-blurb">{product['blurb']}</p>
    <a class="shop-cta" href="{product['url']}" rel="sponsored nofollow noopener">쿠팡에서 보기 &rarr;</a>
  </div>
</li>"""
        )
    store = ""
    if post["store"]:
        st = data["store"]
        store = f"""
<p class="shop-more"><a href="{st['url']}" rel="sponsored nofollow noopener">{st['cta']} &rarr;</a></p>"""
    return f"""
<section class="shop-block" aria-labelledby="shop-head">
  <h2 id="shop-head" class="block-title">이 글에서 소개한 물건</h2>
  <p class="block-note">{data['price_note']}</p>
  <ul class="shop-grid">{''.join(cards)}</ul>
  {store}
  <p class="disclosure-mini">{data['disclosure']}</p>
</section>"""


def kmong_box(post: dict, ctx: dict) -> str:
    keys = post["kmong"]
    if not keys:
        return ""
    data = ctx["kmong"]
    cards = []
    for key in keys:
        service = data["services"].get(key)
        if not service:
            raise SystemExit(f"{post['slug']}: data/kmong.json 에 없는 서비스 키 {key}")
        packages = "".join(
            f'<li><span>{p["name"]}</span><strong>{format_price(p["price"])}</strong>'
            f'<em>{p["spec"]}</em></li>'
            for p in service["packages"]
        )
        points = "".join(f"<li>{p}</li>" for p in service["points"])
        cards.append(
            f"""<li class="gig-card">
  <span class="gig-badge">크몽 · {service['lead']}</span>
  <a class="gig-name" href="{service['url']}" rel="sponsored nofollow noopener">{service['name']}</a>
  <p class="gig-blurb">{service['blurb']}</p>
  <ul class="gig-points">{points}</ul>
  <ul class="gig-packages">{packages}</ul>
  <a class="gig-cta" href="{service['url']}" rel="sponsored nofollow noopener">{format_price(service['from'])}부터 · 크몽에서 보기 &rarr;</a>
</li>"""
        )
    return f"""
<section class="gig-block" aria-labelledby="gig-head">
  <h2 id="gig-head" class="block-title">제작 의뢰 — 크몽 '오몽이'</h2>
  <p class="block-note">{data['seller']['blurb']}</p>
  <ul class="gig-grid">{''.join(cards)}</ul>
  <p class="disclosure-mini">{data['seller']['note']}</p>
</section>"""


def cpa_box(post: dict, ctx: dict) -> str:
    """제휴(CPA) 상담·견적 카드. 링크가 하나도 없으면 상자를 그리지 않습니다."""
    keys = post["cpa"]
    if not keys:
        return ""
    data = ctx["cpa"]
    cards = []
    for key in keys:
        campaign = data["campaigns"].get(key)
        if not campaign:
            raise SystemExit(f"{post['slug']}: data/cpa.json 에 없는 캠페인 키 {key}")
        points = "".join(f"<li>{p}</li>" for p in campaign.get("points", []) if p)
        if points:
            points = f'<ul class="gig-points">{points}</ul>'
        url = campaign.get("url", "")
        if url:
            name_html = (
                f'<a class="gig-name" href="{url}" rel="sponsored nofollow noopener">'
                f'{campaign["name"]}</a>'
            )
            cta = (
                f'<a class="gig-cta" href="{url}" rel="sponsored nofollow noopener">'
                f'{campaign.get("cta", "상담 신청 페이지로")} &rarr;</a>'
            )
        else:
            name_html = f'<span class="gig-name">{campaign["name"]}</span>'
            cta = ""
        cards.append(
            f"""<li class="gig-card">
  <span class="gig-badge">제휴 · {campaign.get('platform', '')}</span>
  {name_html}
  <p class="gig-blurb">{campaign.get('blurb', '')}</p>
  {points}
  {cta}
</li>"""
        )
    if not any(data["campaigns"][k].get("url") for k in keys):
        return ""
    return f"""
<section class="gig-block" aria-labelledby="cpa-head">
  <h2 id="cpa-head" class="block-title">이 글에서 말한 상담·견적 신청</h2>
  <p class="block-note">{data['note']}</p>
  <ul class="gig-grid">{''.join(cards)}</ul>
  <p class="disclosure-mini">{data['disclosure']}</p>
</section>"""


def hire_strip(ctx: dict, variant: str = "post") -> str:
    """제작 의뢰 띠. variant="home"은 홈 상단, "post"는 글 하단."""
    base = ctx["site"]["base_path"]
    if variant == "home":
        eyebrow = "제작 의뢰"
        title = "롱폼·숏폼·자체 홍보곡, AI로 만들어 드립니다"
        body = (
            "유튜브 홍보영상·브랜드 필름 같은 롱폼, 릴스·틱톡·쇼츠 광고 숏폼, "
            "브랜드에 맞춘 오리지널 홍보곡까지. 숏폼은 3일부터, 롱폼과 홍보곡은 "
            "조건 확인 후 고정 금액으로 견적드립니다."
        )
        label = "패키지와 의뢰 방법"
    else:
        eyebrow = "제작 의뢰"
        title = "이런 영상이 필요하면 맡기실 수 있습니다"
        body = (
            "브랜드·스타트업·소상공인 홍보영상(롱폼), 릴스·틱톡·쇼츠 광고(숏폼), "
            "자체 홍보곡(BGM)까지 AI로 제작합니다. 준비물과 납품 규격, 비용 기준을 "
            "한 페이지에 정리해 두었습니다."
        )
        label = "제작 의뢰 안내 보기"
    return f"""<section class="hire-strip hire-{variant}">
  <div class="hire-copy">
    <span class="hire-eyebrow">{eyebrow}</span>
    <h2>{title}</h2>
    <p>{body}</p>
  </div>
  <a class="button hire-cta" href="{base}services/">{label} &rarr;</a>
</section>"""


def store_strip(ctx: dict) -> str:
    st = ctx["coupang"]["store"]
    return f"""<section class="store-strip">
  <div>
    <span class="store-badge">{st['badge']}</span>
    <h2>{st['name']}</h2>
    <p>{st['blurb']}</p>
  </div>
  <a class="button" href="{st['url']}" rel="sponsored nofollow noopener">{st['cta']} &rarr;</a>
</section>"""


def sidebar_html(post: dict, ctx: dict) -> str:
    parts = []
    if post["toc"]:
        links = "".join(
            f'<li class="lvl{item["level"]}"><a href="#{item["id"]}">{escape(item["label"])}</a></li>'
            for item in post["toc"]
        )
        parts.append(
            f'<nav class="side-box toc-box" aria-label="목차"><h2>이 글의 목차</h2>'
            f'<ul class="toc">{links}</ul></nav>'
        )
    if post["coupang"]:
        rows = "".join(
            f'<li><a href="{ctx["coupang"]["products"][k]["url"]}" rel="sponsored nofollow noopener">'
            f'{escape(ctx["coupang"]["products"][k]["name"])}</a></li>'
            for k in post["coupang"]
        )
        parts.append(
            f'<div class="side-box"><h2>소개한 물건</h2><ul class="side-links">{rows}</ul>'
            f'<p class="disclosure-mini">{ctx["coupang"]["disclosure"]}</p></div>'
        )
    if post["kmong"]:
        rows = "".join(
            f'<li><a href="{ctx["kmong"]["services"][k]["url"]}" rel="sponsored nofollow noopener">'
            f'{escape(ctx["kmong"]["services"][k]["name"])}</a></li>'
            for k in post["kmong"]
        )
        parts.append(
            f'<div class="side-box"><h2>제작 의뢰</h2><ul class="side-links">{rows}</ul></div>'
        )
    live_cpa = [k for k in post["cpa"] if ctx["cpa"]["campaigns"].get(k, {}).get("url")]
    if live_cpa:
        rows = "".join(
            f'<li><a href="{ctx["cpa"]["campaigns"][k]["url"]}" rel="sponsored nofollow noopener">'
            f'{escape(ctx["cpa"]["campaigns"][k]["name"])}</a></li>'
            for k in live_cpa
        )
        parts.append(
            f'<div class="side-box"><h2>상담·견적 신청</h2><ul class="side-links">{rows}</ul>'
            f'<p class="disclosure-mini">{ctx["cpa"]["disclosure"]}</p></div>'
        )
    return "".join(parts)


# ---------------------------------------------------------------- 사이트

def build() -> int:
    site = load_json(DATA / "site.json")
    coupang = load_json(DATA / "coupang.json")
    kmong = load_json(DATA / "kmong.json")
    cpa = load_json(DATA / "cpa.json")
    ctx = {"site": site, "coupang": coupang, "kmong": kmong, "cpa": cpa}
    posts = load_posts(ctx)
    categories = {c["key"]: c for c in site["categories"]}

    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True)
    (OUT / ".nojekyll").write_text("", encoding="utf-8")

    base_html = read_template("base.html")
    base = site["base_path"]
    brand = site["brand"]
    year = datetime.now(KST).year

    # AdSense 슬롯: data/site.json 의 adsense_client 가 채워졌을 때만 <head> 에 스크립트를 넣습니다.
    client = (site.get("adsense_client") or "").strip()
    if client:
        if not re.fullmatch(r"ca-pub-\d{16}", client):
            raise SystemExit(f"adsense_client 값이 형식과 다릅니다: {client!r} (ca-pub-XXXXXXXXXXXXXXXX)")
        adsense = (
            f'<script async src="https://pagead2.googlesyndication.com/pagead/js/adsbygoogle.js?client={client}" '
            'crossorigin="anonymous"></script>'
        )
    else:
        adsense = ""

    def page(*, title: str, description: str, canonical: str, content: str,
             current: str = "", og_type: str = "website", head_extra: str = "",
             body_class: str = "") -> str:
        return render(
            base_html,
            {
                "lang": site["language"],
                "locale": site["locale"],
                "brand": brand,
                "tagline": site["tagline"],
                "title": html.escape(title, quote=True),
                "description": html.escape(description, quote=True),
                "canonical": canonical,
                "og_type": og_type,
                "base": base,
                "nav": nav_html(ctx, current),
                "content": content,
                "year": year,
                "footer_note": site["footer_note"],
                "adsense": adsense,
                "head_extra": head_extra,
                "body_class": body_class,
            },
        )

    def write(rel: str, text: str) -> None:
        target = OUT / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")

    # ---- 글
    for post in posts:
        url = f"{site['base_url'].rstrip('/')}/posts/{post['slug']}/"
        post["url"] = url
        related = [p for p in posts if p["slug"] != post["slug"]][:2]
        related_html = ""
        if related:
            links = "".join(
                f'<li><a href="{base}posts/{p["slug"]}/">{escape(p["title"])}</a>'
                f'<span>{escape(p["category_label"])} · {p["reading"]}분</span></li>'
                for p in related
            )
            related_html = (
                f'<section class="related"><h2 class="block-title">이어서 읽기</h2><ul>{links}</ul></section>'
            )

        toc_html = ""
        if post["toc"]:
            jumps = "".join(
                f'<a href="#{item["id"]}">{escape(item["label"])}</a>' for item in post["toc"]
            )
            toc_html = (
                '<details class="jump" open><summary>이 글의 목차</summary>'
                f'<div class="jump-links">{jumps}</div></details>'
            )

        article = f"""
<article class="post">
  <nav class="crumbs" aria-label="위치"><a href="{base}">글</a> / <a href="{base}category/{post['category']}/">{escape(post['category_label'])}</a></nav>
  <header class="post-head">
    <p class="eyebrow">{escape(post['category_label'])}</p>
    <h1>{escape(post['title'])}</h1>
    <p class="dek">{escape(post['excerpt'])}</p>
    <p class="meta"><time datetime="{post['date']}">{fmt_date(post['date'])}</time> · {post['chars']:,}자 · 읽는 데 {post['reading']}분</p>
  </header>
  {toc_html}
  <div class="prose">
    {post['html']}
  </div>
  {coupang_box(post, ctx)}
  {kmong_box(post, ctx)}
  {cpa_box(post, ctx)}
  {hire_strip(ctx)}
  {related_html}
  <p class="post-foot"><a href="{base}">글 전체 보기</a></p>
</article>"""

        image_url = post["meta"].get("image", "")
        if image_url.startswith("/"):
            origin = f"{urlparse(site['base_url']).scheme}://{urlparse(site['base_url']).netloc}"
            image_url = origin + image_url

        jsonld = {
            "@context": "https://schema.org",
            "@type": "BlogPosting",
            "headline": post["title"],
            "description": post["description"],
            "datePublished": post["date"],
            "dateModified": post["date"],
            "inLanguage": "ko-KR",
            "mainEntityOfPage": {"@type": "WebPage", "@id": url},
            "author": {"@type": "Organization", "name": brand,
                       "url": site["base_url"].rstrip("/") + "/"},
            "publisher": {"@type": "Organization", "name": brand},
            "articleSection": post["category_label"],
            "keywords": ", ".join(post["tags"]),
        }
        if image_url:
            jsonld["image"] = image_url
        head_extra = (
            f'<meta property="article:published_time" content="{post["date"]}">\n'
            f'<meta name="author" content="{html.escape(brand, quote=True)}">\n'
            + (
                f'<meta property="og:image" content="{image_url}">\n'
                f'<meta name="twitter:image" content="{image_url}">\n'
                if image_url else ""
            )
            + f'<script type="application/ld+json">{json.dumps(jsonld, ensure_ascii=False)}</script>'
        )

        content = article + f'\n<aside class="post-aside">{sidebar_html(post, ctx)}</aside>'
        write(
            f"posts/{post['slug']}/index.html",
            page(
                title=f"{post['title']} · {brand}",
                description=post["description"],
                canonical=url,
                content=content,
                current="journal",
                og_type="article",
                head_extra=head_extra,
                body_class="layout-post",
            ),
        )

    # ---- 카테고리
    by_category: dict[str, list[dict]] = {}
    for post in posts:
        by_category.setdefault(post["category"], []).append(post)

    for key, items in by_category.items():
        label = categories[key]["label"]
        listing = "".join(
            f"""<li class="entry">
  <a href="{base}posts/{p['slug']}/">
    <span class="entry-cat">{escape(p['category_label'])}</span>
    <h2>{escape(p['title'])}</h2>
    <p>{escape(p['excerpt'])}</p>
    <span class="entry-meta"><time datetime="{p['date']}">{fmt_date(p['date'], True)}</time> · {p['reading']}분</span>
  </a>
</li>"""
            for p in items
        )
        url = f"{site['base_url'].rstrip('/')}/category/{key}/"
        body = f"""
<header class="page-head">
  <p class="crumbs"><a href="{base}">글</a> / 카테고리</p>
  <h1>{escape(label)}</h1>
  <p class="dek">{escape(categories[key]['desc'])} 글 {len(items)}편.</p>
</header>
<ul class="entries">{listing}</ul>
<p class="post-foot"><a href="{base}">글 전체 보기</a></p>"""
        write(
            f"category/{key}/index.html",
            page(
                title=f"{label} · {brand}",
                description=categories[key]["desc"],
                canonical=url,
                content=body,
                current="journal",
            ),
        )

    # ---- 고정 페이지
    for spec in site["pages"]:
        rel = spec["path"].strip("/")
        if not rel:
            continue
        src = ROOT / "pages" / f"{spec['key']}.md"
        meta, body = parse_front_matter(src.read_text(encoding="utf-8"))
        body_html, _ = md_to_html(body, ctx)
        url = f"{site['base_url'].rstrip('/')}/{rel}/"
        content = f"""
<article class="page">
  <header class="page-head">
    <p class="crumbs"><a href="{base}">글</a></p>
    <h1>{escape(meta['title'])}</h1>
  </header>
  <div class="prose">{body_html}</div>
</article>"""
        write(
            f"{rel}/index.html",
            page(
                title=f"{meta['title']} · {brand}",
                description=meta.get("description", site["description"]),
                canonical=url,
                content=content,
                current=spec["key"],
            ),
        )

    # ---- 홈
    featured = next((p for p in posts if p["featured"]), posts[0] if posts else None)
    rest = [p for p in posts if featured and p["slug"] != featured["slug"]]
    hero = ""
    if featured:
        hero = f"""
<section class="hero">
  <div class="hero-text">
    <p class="eyebrow">최근 글</p>
    <h1><a href="{base}posts/{featured['slug']}/">{escape(featured['title'])}</a></h1>
    <p class="dek">{escape(featured['excerpt'])}</p>
    <p class="meta"><time datetime="{featured['date']}">{fmt_date(featured['date'])}</time> · 읽는 데 {featured['reading']}분</p>
    <a class="button" href="{base}posts/{featured['slug']}/">글 읽기</a>
  </div>
</section>"""

    listing = "".join(
        f"""<li class="card">
  <a href="{base}posts/{p['slug']}/">
    <span class="entry-cat">{escape(p['category_label'])}</span>
    <h2>{escape(p['title'])}</h2>
    <p>{escape(p['excerpt'])}</p>
    <span class="entry-meta"><time datetime="{p['date']}">{fmt_date(p['date'], True)}</time> · {p['reading']}분</span>
  </a>
</li>"""
        for p in rest
    )
    cat_links = "".join(
        f'<a class="pill" href="{base}category/{key}/">{escape(categories[key]["label"])} '
        f'<span>{len(items)}</span></a>'
        for key, items in sorted(by_category.items(), key=lambda kv: -len(kv[1]))
    )
    index_content = f"""
{hero}
<section class="intro">
  <h2 class="block-title">이 블로그는</h2>
  <p>{escape(site['about_blurb'])}</p>
  <div class="pills">{cat_links}</div>
</section>
<section class="latest">
  <h2 class="block-title">지난 글</h2>
  <ul class="cards">{listing}</ul>
</section>
{hire_strip(ctx, 'home')}
{store_strip(ctx)}"""

    blog_ld = {
        "@context": "https://schema.org",
        "@type": "Blog",
        "name": brand,
        "description": site["description"],
        "url": site["base_url"].rstrip("/") + "/",
        "inLanguage": "ko-KR",
        "blogPost": [
            {"@type": "BlogPosting", "headline": p["title"],
             "datePublished": p["date"], "url": p["url"]}
            for p in posts
        ],
    }
    write(
        "index.html",
        page(
            title=f"{brand} — {site['tagline']}",
            description=site["description"],
            canonical=site["base_url"].rstrip("/") + "/",
            content=index_content,
            current="journal",
            head_extra=f'<script type="application/ld+json">{json.dumps(blog_ld, ensure_ascii=False)}</script>',
            body_class="layout-home",
        ),
    )

    # ---- 피드
    items = "".join(
        f"""  <item>
    <title>{escape(p['title'])}</title>
    <link>{p['url']}</link>
    <guid isPermaLink="true">{p['url']}</guid>
    <pubDate>{rfc822(p['date'])}</pubDate>
    <category>{escape(p['category_label'])}</category>
    <description>{escape(p['excerpt'])}</description>
  </item>
"""
        for p in posts
    )
    last = posts[0]["date"] if posts else datetime.now(KST).strftime("%Y-%m-%d")
    write(
        "rss.xml",
        f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">
<channel>
  <title>{escape(brand)}</title>
  <link>{site['base_url'].rstrip('/')}/</link>
  <atom:link href="{site['base_url'].rstrip('/')}/rss.xml" rel="self" type="application/rss+xml"/>
  <description>{escape(site['description'])}</description>
  <language>ko</language>
  <lastBuildDate>{rfc822(last)}</lastBuildDate>
{items}</channel>
</rss>
""",
    )

    urls = [
        ("", last),
        *[(f"posts/{p['slug']}/", p["date"]) for p in posts],
        *[(f"category/{key}/", max(i["date"] for i in items_))
          for key, items_ in by_category.items()],
        *[(f"{spec['path'].strip('/')}/", last)
          for spec in site["pages"] if spec["path"].strip("/")],
    ]
    body = "".join(
        f"  <url><loc>{site['base_url'].rstrip('/')}/{path}</loc><lastmod>{lastmod}</lastmod></url>\n"
        for path, lastmod in urls
    )
    write(
        "sitemap.xml",
        f"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
{body}</urlset>
""",
    )

    write("robots.txt", f"User-agent: *\nAllow: /\n\nSitemap: {site['base_url'].rstrip('/')}/sitemap.xml\n")

    write(
        "404.html",
        page(
            title=f"페이지를 찾을 수 없습니다 · {brand}",
            description="주소가 바뀌었거나 없는 페이지입니다.",
            canonical=site["base_url"].rstrip("/") + "/404.html",
            content=f'<article class="page"><h1>페이지를 찾을 수 없습니다</h1>'
                    f'<p class="dek">주소가 바뀌었거나 삭제된 글입니다. 홈에서 전체 글을 볼 수 있습니다.</p>'
                    f'<p><a class="button" href="{base}">글 목록으로</a></p></article>',
        ),
    )

    # ---- 정적 자산
    for path in STATIC.rglob("*"):
        if path.is_file():
            target = OUT / "assets" / path.relative_to(STATIC)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)

    # ---- 리포트
    print(f"빌드 완료 — 글 {len(posts)}편, 카테고리 {len(by_category)}개 → {OUT}")
    print(f"  AdSense 슬롯: {'켜짐 (' + client + ')' if client else '비어 있음 — 스크립트 미출력'}")
    problems = []
    for post in posts:
        flag = ""
        if post["chars"] < CHAR_MIN or post["chars"] > CHAR_MAX:
            flag = "  <-- 1,200~2,000자 범위 밖"
            problems.append(post["slug"])
        print(f"  {post['chars']:>5}자  {post['slug']}{flag}")
    if "--check" in sys.argv and problems:
        raise SystemExit(f"글자수 범위를 벗어난 글: {problems}")
    return 0


if __name__ == "__main__":
    raise SystemExit(build())
