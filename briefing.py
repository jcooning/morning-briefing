"""아침 브리핑 생성기 - briefing.txt 를 새로 씁니다."""
import html
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime
from zoneinfo import ZoneInfo

import yfinance as yf

KST = ZoneInfo("Asia/Seoul")
WEEKDAYS = "월화수목금토일"

# (표시 이름, 야후 파이낸스 코드, 단위)
MARKET = [
    ("코스피", "^KS11", "idx"),
    ("코스닥", "^KQ11", "idx"),
    ("S&P500", "^GSPC", "idx"),
    ("나스닥", "^IXIC", "idx"),
    ("달러", "KRW=X", "fx"),
    ("유로", "EURKRW=X", "fx"),
    ("엔화(100엔)", "JPYKRW=X", "fx100"),
    ("비트코인", "BTC-KRW", "krw"),
]
STOCKS = [
    ("SK하이닉스", "000660.KS", "krw"),
    ("삼성전자", "005930.KS", "krw"),
    ("구글", "GOOGL", "usd"),
    ("애플", "AAPL", "usd"),
]


def fmt_price(value, unit):
    if unit == "krw":
        return f"{value:,.0f}원"
    if unit == "usd":
        return f"${value:,.2f}"
    if unit == "fx":
        return f"{value:,.1f}원"
    if unit == "fx100":
        return f"{value * 100:,.1f}원"
    return f"{value:,.2f}"


def fmt_change(last, prev):
    pct = (last - prev) / prev * 100
    arrow = "▲" if pct > 0 else ("▼" if pct < 0 else "-")
    return f"{arrow}{abs(pct):.1f}%"


def market_section():
    items = MARKET + STOCKS
    codes = [code for _, code, _ in items]
    closes = yf.download(codes, period="10d", progress=False, auto_adjust=False)["Close"]

    lines = ["📈 증시"]
    for i, (name, code, unit) in enumerate(items):
        if i in (4, 7, len(MARKET)):
            lines.append("")  # 지수 / 환율 / 코인 / 종목 사이 빈 줄
        try:
            series = closes[code].dropna() if code in closes else None
            if series is None or len(series) < 2:
                series = yf.Ticker(code).history(period="10d")["Close"].dropna()  # 재시도
            last, prev = float(series.iloc[-1]), float(series.iloc[-2])
            lines.append(f"{name} {fmt_price(last, unit)} ({fmt_change(last, prev)})")
        except Exception:
            lines.append(f"{name} 조회 실패")
    return "\n".join(lines)


# 뉴스: 분야별로 위에서부터 시도하고, 다 안 되면 구글 뉴스 검색으로 대체
NEWS = [
    ("📰 정치", [
        ("MBC", "https://imnews.imbc.com/rss/google_news/politics.rss"),
        ("JTBC", "https://news-ex.jtbc.co.kr/v1/get/rss/section/politics"),
    ], "정치"),
    ("💰 경제", [
        ("MBC", "https://imnews.imbc.com/rss/google_news/economy.rss"),
        ("JTBC", "https://news-ex.jtbc.co.kr/v1/get/rss/section/economy"),
    ], "경제"),
    ("💻 IT", [
        ("전자신문", "https://rss.etnews.com/Section901.xml"),
        ("ZDNet", "https://feeds.feedburner.com/zdkorea"),
    ], "IT 인공지능"),
    ("🏠 부동산", [
        ("한국경제", "https://www.hankyung.com/feed/realestate"),
        ("매일경제", "https://www.mk.co.kr/rss/50300009/"),
    ], "부동산 청약"),
]
NEWS_PER_SECTION = 3      # 분야별로 보여줄 기사 수
NEWS_CANDIDATES = 12      # AI에게 넘겨줄 분야별 후보 기사 수
# 먼저 시도할 모델 (안 되면 구글에서 지금 쓸 수 있는 flash 모델 목록을 받아 차례로 시도)
GEMINI_PREFERRED = ["gemini-flash-latest", "gemini-flash-lite-latest"]
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/130 Safari/537.36"}


def clean(text):
    text = html.unescape(re.sub(r"<[^>]+>", " ", text or ""))
    return re.sub(r"\s+", " ", text).strip()


def fetch_items(url):
    """RSS에서 (제목, 설명) 목록을 가져옵니다."""
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=15) as resp:
        root = ET.fromstring(resp.read())
    items = []
    for node in root.iter():
        if node.tag.split("}")[-1] not in ("item", "entry"):
            continue
        title = desc = ""
        for child in node:
            tag = child.tag.split("}")[-1]
            if tag == "title":
                title = clean(child.text)
            elif tag in ("description", "summary") and not desc:
                desc = clean(child.text)[:300]
        if title:
            items.append((title, desc))
    return items


def google_news(query):
    q = urllib.parse.quote(f"{query} when:1d")
    url = f"https://news.google.com/rss/search?q={q}&hl=ko&gl=KR&ceid=KR:ko"
    results = []
    for title, _ in fetch_items(url):
        head, _, source = title.rpartition(" - ")  # 구글 뉴스 제목 끝의 " - 언론사" 분리
        results.append((source or "뉴스", head or title, ""))
    return results


def collect_news():
    """분야별 후보 기사 [(분야, [(출처, 제목, 설명), ...]), ...]"""
    collected = []
    for heading, feeds, query in NEWS:
        picked = []
        for source, url in feeds:
            try:
                picked = [(source, t, d) for t, d in fetch_items(url)]
            except Exception:
                picked = []
            if picked:
                break
        if not picked:
            try:
                picked = google_news(query)
            except Exception:
                picked = []
        seen, unique = set(), []
        for item in picked:
            if item[1] not in seen:
                seen.add(item[1])
                unique.append(item)
        collected.append((heading, unique[:NEWS_CANDIDATES]))
    return collected


def gemini_models(key):
    """시도할 모델 순서: 선호 모델 → 지금 쓸 수 있는 flash 모델들"""
    models = list(GEMINI_PREFERRED)
    try:
        req = urllib.request.Request("https://generativelanguage.googleapis.com/v1beta/models?pageSize=100",
                                     headers={"x-goog-api-key": key})
        with urllib.request.urlopen(req, timeout=20) as resp:
            listing = json.loads(resp.read().decode("utf-8"))
        found = []
        for m in listing.get("models", []):
            name = m.get("name", "").removeprefix("models/")
            if ("flash" in name and "generateContent" in m.get("supportedGenerationMethods", [])
                    and not any(x in name for x in ("image", "tts", "live", "audio", "embed", "preview", "exp"))):
                found.append(name)
        found.sort(reverse=True)  # 숫자가 큰(최신) 모델부터
        models += [n for n in found if n not in models]
    except Exception as e:
        print(f"[Gemini 모델 목록 실패] {e}")
    return models[:6]


def ai_summarize(collected):
    """Gemini가 분야별로 중요한 기사를 골라 짧게 요약합니다."""
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        raise RuntimeError("GEMINI_API_KEY 없음")

    material = []
    for i, (heading, items) in enumerate(collected):
        material.append(f"## 분야 {i}: {heading}")
        for j, (source, title, desc) in enumerate(items):
            material.append(f"[{j}] ({source}) {title} — {desc}")
    prompt = (
        "너는 아침 뉴스 브리핑 편집자다. 아래 분야별 기사 목록에서, 분야마다 "
        f"오늘 아침 꼭 알아야 할 중요한 기사 {NEWS_PER_SECTION}개를 골라라.\n"
        "- 광고성·가벼운 화제·같은 사건의 중복 기사는 제외한다.\n"
        "- 각 기사를 사실 위주로 한국어 1~2문장(90자 이내)으로 요약한다. 제목을 그대로 옮기지 말고 핵심 내용을 풀어 쓴다.\n"
        "- 주어진 제목과 설명에 없는 내용은 추측해서 쓰지 않는다. 의견이나 평가는 덧붙이지 않는다.\n"
        "- 출처는 목록에 적힌 그대로 쓴다.\n"
        "- 분야에 기사가 없으면 items를 빈 배열로 둔다.\n"
        'JSON으로만 답하라: {"sections":[{"index":0,"items":[{"summary":"...","source":"..."}]}]}\n\n'
        + "\n".join(material)
    )
    body = json.dumps({
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"responseMimeType": "application/json", "temperature": 0.3},
    }).encode("utf-8")
    errors = []
    text = None
    for model in gemini_models(key):
        for attempt in range(3):  # 503(사용량 몰림)이면 잠깐 쉬었다가 재시도
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
            req = urllib.request.Request(url, data=body, method="POST",
                                         headers={"Content-Type": "application/json", "x-goog-api-key": key})
            try:
                with urllib.request.urlopen(req, timeout=90) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                text = data["candidates"][0]["content"]["parts"][0]["text"]
                break
            except urllib.error.HTTPError as e:
                detail = e.read().decode("utf-8", "replace")[:300]
                print(f"[Gemini {model}] HTTP {e.code}: {detail}")  # Actions 로그에서 확인용
                if e.code in (500, 503) and attempt < 2:
                    time.sleep(15)
                    continue
                errors.append(f"{model} {e.code}")
                break
            except Exception as e:
                print(f"[Gemini {model}] {type(e).__name__}: {e}")
                errors.append(f"{model} {type(e).__name__}")
                break
        if text is not None:
            break
    if text is None:
        raise RuntimeError(", ".join(errors))
    text = text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    result = json.loads(text)

    blocks = []
    by_index = {s.get("index"): s.get("items", []) for s in result.get("sections", [])}
    for i, (heading, _) in enumerate(collected):
        lines = [heading]
        for it in by_index.get(i, [])[:NEWS_PER_SECTION]:
            lines.append(f"· {it['summary']} [{it['source']}]")
        if len(lines) == 1:
            lines.append("· 가져오지 못했어요")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def headlines_only(collected):
    """AI 요약이 실패하면 제목만이라도 보여줍니다."""
    blocks = []
    for heading, items in collected:
        lines = [heading] + [f"· {t} [{s}]" for s, t, _ in items[:NEWS_PER_SECTION]]
        if len(lines) == 1:
            lines.append("· 가져오지 못했어요")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def news_section():
    collected = collect_news()
    try:
        return ai_summarize(collected)
    except Exception as e:
        return headlines_only(collected) + f"\n(AI 요약 실패: {e})"


def gold_section():
    """국제 금 시세 (뉴욕 금 선물, 달러/온스)"""
    series = yf.download(["GC=F"], period="10d", progress=False, auto_adjust=False)["Close"]["GC=F"].dropna()
    last, prev = float(series.iloc[-1]), float(series.iloc[-2])
    return f"🥇 금시세\n국제 금 ${last:,.2f}/oz ({fmt_change(last, prev)})"


def main():
    now = datetime.now(KST)
    header = f"☀ {now.month}/{now.day}({WEEKDAYS[now.weekday()]}) 아침 브리핑"

    sections = [header]
    try:
        sections.append(market_section())
    except Exception as e:
        sections.append(f"📈 증시\n조회 실패 ({type(e).__name__})")
    try:
        sections.append(news_section())
    except Exception as e:
        sections.append(f"📰 뉴스\n조회 실패 ({type(e).__name__})")
    try:
        sections.append(gold_section())
    except Exception as e:
        sections.append(f"🥇 금시세\n국제 금 조회 실패 ({type(e).__name__})")

    with open("briefing.txt", "w", encoding="utf-8") as f:
        f.write("\n\n".join(sections) + "\n")
    print("\n\n".join(sections))


if __name__ == "__main__":
    main()
