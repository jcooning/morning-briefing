"""아침 브리핑 생성기 - briefing.txt 를 새로 씁니다."""
import re
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
    ("🗞 딴지일보", [
        ("딴지일보", "https://www.ddanzi.com/ddanziNews/rss"),
        ("딴지일보", "https://www.ddanzi.com/rss"),
    ], "딴지일보"),
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
NEWS_PER_SECTION = 3
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/130 Safari/537.36"}


def fetch_titles(url):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=15) as resp:
        root = ET.fromstring(resp.read())
    titles = []
    for item in root.iter():
        if item.tag.split("}")[-1] in ("item", "entry"):
            for child in item:
                if child.tag.split("}")[-1] == "title" and child.text:
                    titles.append(re.sub(r"\s+", " ", child.text).strip())
                    break
    return titles


def google_news(query):
    q = urllib.parse.quote(f"{query} when:1d")
    url = f"https://news.google.com/rss/search?q={q}&hl=ko&gl=KR&ceid=KR:ko"
    results = []
    for t in fetch_titles(url):
        title, _, source = t.rpartition(" - ")  # 구글 뉴스 제목 끝의 " - 언론사" 분리
        results.append((source or "뉴스", title or t))
    return results


def news_section():
    blocks = []
    for heading, feeds, query in NEWS:
        picked = []
        for source, url in feeds:
            try:
                picked = [(source, t) for t in fetch_titles(url)]
            except Exception:
                picked = []
            if picked:
                break
        if not picked:
            try:
                picked = google_news(query)
            except Exception:
                picked = []
        lines = [heading]
        seen = set()
        for source, title in picked:
            if title in seen:
                continue
            seen.add(title)
            lines.append(f"· {title} [{source}]")
            if len(lines) > NEWS_PER_SECTION:
                break
        if len(lines) == 1:
            lines.append("· 가져오지 못했어요")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


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
