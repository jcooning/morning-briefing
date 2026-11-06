"""아침 브리핑 생성기 - briefing.txt / briefing2.txt 를 새로 씁니다."""
import html
import json
import os
import random
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
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
KR_STOCKS = [  # 국내 관심종목
    ("SK하이닉스", "000660.KS", "krw"),
    ("삼성전자", "005930.KS", "krw"),
    ("LG전자", "066570.KS", "krw"),
    ("하이브", "352820.KS", "krw"),
]
US_STOCKS = [  # 미국 관심종목
    ("구글", "GOOGL", "usd"),
    ("애플", "AAPL", "usd"),
    ("엔비디아", "NVDA", "usd"),
    ("메타", "META", "usd"),
    ("TSMC", "TSM", "usd"),
    ("스페이스X", "SPCX", "usd"),
    ("팔란티어", "PLTR", "usd"),
]
STOCKS = KR_STOCKS + US_STOCKS


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


# ---------------- 날씨 (Open-Meteo, 서울) ----------------
WEATHER_URL = ("https://api.open-meteo.com/v1/forecast?latitude=37.5665&longitude=126.978"
               "&daily=weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max"
               "&timezone=Asia%2FSeoul&forecast_days=4")
WEATHER_CODES = [  # (코드 상한, 이모지, 상태)
    (0, "☀️", "맑음"), (2, "🌤", "구름 조금"), (3, "☁️", "흐림"), (48, "🌫", "안개"),
    (57, "🌦", "이슬비"), (67, "🌧", "비"), (77, "🌨", "눈"), (82, "🌧", "소나기"),
    (86, "🌨", "눈"), (99, "⛈", "뇌우"),
]


def weather_desc(code):
    for limit, emoji, text in WEATHER_CODES:
        if code <= limit:
            return emoji, text
    return "🌡", "-"


def get_weather():
    req = urllib.request.Request(WEATHER_URL, headers=UA)
    with urllib.request.urlopen(req, timeout=15) as resp:
        d = json.loads(resp.read().decode("utf-8"))["daily"]
    days = []
    for i, day in enumerate(d["time"]):
        emoji, text = weather_desc(int(d["weather_code"][i]))
        days.append({
            "date": datetime.strptime(day, "%Y-%m-%d"), "emoji": emoji, "text": text,
            "max": round(d["temperature_2m_max"][i]), "min": round(d["temperature_2m_min"][i]),
            "rain": d["precipitation_probability_max"][i] or 0,
        })
    return days


def weather_line(day):
    return f"{day['emoji']} {day['text']} · 최고 {day['max']}° / 최저 {day['min']}° · 강수 {day['rain']}%"


def today_weather(days):
    return "서울 " + weather_line(days[0])


def weekend_weather(days):
    lines = ["🗓 주말 날씨 (서울)"]
    for day in days[1:3]:  # 금요일 기준 토·일
        lines.append(f"{day['date'].month}/{day['date'].day}({WEEKDAYS[day['date'].weekday()]}) {weather_line(day)}")
    return "\n".join(lines)


# ---------------- 금리 ----------------
def ecos(stat, cycle, start, end, item):
    """한국은행 경제통계(ECOS) 조회 — 마지막 값들"""
    url = f"https://ecos.bok.or.kr/api/StatisticSearch/sample/json/kr/1/10/{stat}/{cycle}/{start}/{end}/{item}"
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=15) as resp:
        rows = json.loads(resp.read().decode("utf-8"))["StatisticSearch"]["row"]
    return [float(r["DATA_VALUE"]) for r in rows]


def rate_change(last, prev):
    diff = last - prev
    arrow = "▲" if diff > 0 else ("▼" if diff < 0 else "-")
    return f"{arrow}{abs(diff):.2f}"


def rates_line(closes):
    now = datetime.now(KST)
    parts = []
    try:
        base = ecos("722Y001", "M", f"{now.year - 1}{now.month:02d}", f"{now.year}{now.month:02d}", "0101000")
        parts.append(f"기준 {base[-1]:.2f}%")
    except Exception as e:
        print(f"[금리 기준] 실패: {type(e).__name__}")
    try:
        start = (now - timedelta(days=9)).strftime("%Y%m%d")
        govt = ecos("817Y002", "D", start, now.strftime("%Y%m%d"), "010200000")
        parts.append(f"국고3년 {govt[-1]:.2f}% ({rate_change(govt[-1], govt[-2])})")
    except Exception as e:
        print(f"[금리 국고3년] 실패: {type(e).__name__}")
    try:
        series = closes["^TNX"].dropna() if "^TNX" in closes else None
        if series is None or len(series) < 2:
            series = yf.Ticker("^TNX").history(period="10d")["Close"].dropna()
        parts.append(f"미국10년 {float(series.iloc[-1]):.2f}% ({rate_change(float(series.iloc[-1]), float(series.iloc[-2]))})")
    except Exception as e:
        print(f"[금리 미국10년] 실패: {type(e).__name__}")
    return "금리 " + " · ".join(parts) if parts else "금리 조회 실패"


# ---------------- 오늘의 말씀 (개역한글) ----------------
# (책 번호, 책 이름, 장, 절) — 날짜마다 하나씩 무작위로 고름
VERSES = [
    (1, "창세기", 1, 1), (1, "창세기", 28, 15), (2, "출애굽기", 14, 14), (5, "신명기", 31, 6),
    (6, "여호수아", 1, 9), (9, "사무엘상", 16, 7), (19, "시편", 1, 1), (19, "시편", 16, 8),
    (19, "시편", 18, 2), (19, "시편", 23, 1), (19, "시편", 23, 4), (19, "시편", 27, 1),
    (19, "시편", 34, 8), (19, "시편", 37, 4), (19, "시편", 37, 5), (19, "시편", 46, 1),
    (19, "시편", 46, 10), (19, "시편", 55, 22), (19, "시편", 62, 1), (19, "시편", 90, 12),
    (19, "시편", 91, 1), (19, "시편", 103, 2), (19, "시편", 118, 24), (19, "시편", 119, 105),
    (19, "시편", 121, 1), (19, "시편", 121, 2), (19, "시편", 127, 1), (19, "시편", 133, 1),
    (19, "시편", 139, 14), (19, "시편", 145, 18), (19, "시편", 147, 3), (20, "잠언", 3, 5),
    (20, "잠언", 3, 6), (20, "잠언", 4, 23), (20, "잠언", 16, 3), (20, "잠언", 16, 9),
    (20, "잠언", 17, 17), (20, "잠언", 18, 10), (20, "잠언", 22, 6), (20, "잠언", 27, 17),
    (21, "전도서", 3, 1), (21, "전도서", 4, 9), (23, "이사야", 26, 3), (23, "이사야", 40, 31),
    (23, "이사야", 41, 10), (23, "이사야", 43, 2), (23, "이사야", 43, 19), (23, "이사야", 55, 8),
    (24, "예레미야", 29, 11), (24, "예레미야", 33, 3), (25, "예레미야애가", 3, 22), (25, "예레미야애가", 3, 23),
    (33, "미가", 6, 8), (35, "하박국", 3, 18), (36, "스바냐", 3, 17), (40, "마태복음", 5, 9),
    (40, "마태복음", 5, 14), (40, "마태복음", 6, 33), (40, "마태복음", 6, 34), (40, "마태복음", 7, 7),
    (40, "마태복음", 7, 12), (40, "마태복음", 11, 28), (40, "마태복음", 22, 37), (40, "마태복음", 28, 20),
    (41, "마가복음", 9, 23), (41, "마가복음", 10, 27), (42, "누가복음", 1, 37), (42, "누가복음", 6, 31),
    (43, "요한복음", 1, 5), (43, "요한복음", 3, 16), (43, "요한복음", 8, 32), (43, "요한복음", 10, 10),
    (43, "요한복음", 13, 34), (43, "요한복음", 14, 1), (43, "요한복음", 14, 6), (43, "요한복음", 14, 27),
    (43, "요한복음", 15, 5), (43, "요한복음", 16, 33), (45, "로마서", 5, 8), (45, "로마서", 8, 18),
    (45, "로마서", 8, 28), (45, "로마서", 8, 31), (45, "로마서", 12, 2), (45, "로마서", 12, 12),
    (45, "로마서", 15, 13), (46, "고린도전서", 10, 13), (46, "고린도전서", 13, 4), (46, "고린도전서", 13, 13),
    (46, "고린도전서", 16, 14), (47, "고린도후서", 4, 18), (47, "고린도후서", 5, 17), (47, "고린도후서", 12, 9),
    (48, "갈라디아서", 5, 22), (48, "갈라디아서", 6, 9), (49, "에베소서", 2, 8), (49, "에베소서", 4, 32),
    (50, "빌립보서", 1, 6), (50, "빌립보서", 4, 6), (50, "빌립보서", 4, 7), (50, "빌립보서", 4, 13),
    (50, "빌립보서", 4, 19), (51, "골로새서", 3, 23), (52, "데살로니가전서", 5, 16), (52, "데살로니가전서", 5, 17),
    (52, "데살로니가전서", 5, 18), (55, "디모데후서", 1, 7), (58, "히브리서", 11, 1), (58, "히브리서", 12, 1),
    (58, "히브리서", 13, 8), (59, "야고보서", 1, 5), (59, "야고보서", 1, 17), (59, "야고보서", 4, 8),
    (60, "베드로전서", 5, 7), (62, "요한일서", 1, 9), (62, "요한일서", 4, 18), (62, "요한일서", 4, 19),
    (66, "요한계시록", 21, 4),
]


def bible_section():
    now = datetime.now(KST)
    pick = random.Random(now.strftime("%Y%m%d")).choice(VERSES)  # 같은 날은 같은 구절
    book, name, chapter, verse = pick
    req = urllib.request.Request(f"https://bolls.life/get-verse/KRV/{book}/{chapter}/{verse}/", headers=UA)
    with urllib.request.urlopen(req, timeout=15) as resp:
        text = clean(json.loads(resp.read().decode("utf-8"))["text"])
    return f"📖 오늘의 말씀\n{text}\n— {name} {chapter}:{verse}"


def market_section():
    items = MARKET + STOCKS
    codes = [code for _, code, _ in items] + ["^TNX"]  # ^TNX = 미국 10년 국채금리
    closes = yf.download(codes, period="10d", progress=False, auto_adjust=False)["Close"]

    lines = ["📈 증시"]
    for i, (name, code, unit) in enumerate(items):
        if i in (4, 7, len(MARKET), len(MARKET) + len(KR_STOCKS)):
            lines.append("")  # 지수 / 환율 / 코인 / 국내 종목 / 미국 종목 사이 빈 줄
        if i == 4:
            lines.append(rates_line(closes))  # 금리는 달러 위에
        try:
            series = closes[code].dropna() if code in closes else None
            if series is None or len(series) < 2:
                series = yf.Ticker(code).history(period="10d")["Close"].dropna()  # 재시도
            last, prev = float(series.iloc[-1]), float(series.iloc[-2])
            lines.append(f"{name} {fmt_price(last, unit)} ({fmt_change(last, prev)})")
        except Exception:
            lines.append(f"{name} 조회 실패")
    return "\n".join(lines)


# 뉴스: 분야별로 여러 언론사 기사를 모아 AI에게 넘깁니다.
# MBC는 따로 네이버 뉴스의 'MBC 기사 목록'에서 가져와 AI가 분야를 나눠 우선 고릅니다.
NEWS = [
    ("📰 정치", [
        ("JTBC", "https://news-ex.jtbc.co.kr/v1/get/rss/section/politics"),
    ], "정치"),
    ("💰 경제", [
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
MBC_LIST_PAGES = [  # 네이버 뉴스의 MBC(언론사 번호 214) 기사 목록
    "https://news.naver.com/main/list.naver?mode=LPOD&mid=sec&oid=214&listType=summary",
    "https://media.naver.com/press/214",
    "https://media.naver.com/press/214/ranking",
]
MBC_MAX = 30              # AI에게 넘길 MBC 기사 수
PER_SOURCE = 8            # 언론사별로 AI에게 넘길 후보 기사 수
NEWS_PER_SECTION = 3      # 분야별로 보여줄 기사 수
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


def get_html(url):
    req = urllib.request.Request(url, headers={**UA, "Referer": "https://news.naver.com/"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return resp.read().decode("utf-8", "replace")


def naver_article(aid):
    """네이버 뉴스 기사 페이지에서 (제목, 본문 앞부분)을 꺼냅니다."""
    page = get_html(f"https://n.news.naver.com/mnews/article/214/{aid}")
    def meta(prop):
        m = re.search(rf'<meta[^>]+property="{prop}"[^>]+content="([^"]*)"', page)
        return clean(m.group(1)) if m else ""
    return meta("og:title"), meta("og:description")[:300]


def collect_mbc():
    """네이버 뉴스에서 MBC 최신 기사 [(MBC, 제목, 본문 앞부분), ...]"""
    ids = []
    for url in MBC_LIST_PAGES:
        try:
            page = get_html(url)
        except Exception as e:
            print(f"[MBC 목록] {url} 실패: {type(e).__name__}")
            continue
        found = re.findall(r"article/214/(\d{10})", page) + re.findall(r"oid=214&(?:amp;)?aid=(\d{10})", page)
        for aid in found:
            if aid not in ids:
                ids.append(aid)
        print(f"[MBC 목록] {url} {len(found)}건")
        if len(ids) >= MBC_MAX:
            break
    ids = sorted(ids, reverse=True)[:MBC_MAX]  # 번호가 클수록 최신 기사
    articles = []
    with ThreadPoolExecutor(max_workers=6) as pool:
        for aid, result in zip(ids, pool.map(lambda a: _safe(naver_article, a), ids)):
            if result and result[0]:
                articles.append(("MBC", result[0], result[1]))
    print(f"[MBC 기사] {len(articles)}건")
    return articles


def _safe(func, *args):
    try:
        return func(*args)
    except Exception:
        return None


def collect_news():
    """분야별 후보 기사 [(분야, [(출처, 제목, 설명), ...]), ...]"""
    collected = []
    for heading, feeds, query in NEWS:
        picked = []
        for source, url in feeds:
            try:
                if url.startswith("gnews:"):
                    got = [(source, t, d) for _, t, d in google_news(url[6:])]
                else:
                    got = [(source, t, d) for t, d in fetch_items(url)]
            except Exception as e:
                print(f"[뉴스 {heading} {source}] 실패: {type(e).__name__}")
                got = []
            print(f"[뉴스 {heading} {source}] {len(got)}건")
            picked += got[:PER_SOURCE]
        if not picked:
            try:
                picked = google_news(query)[:PER_SOURCE]
            except Exception:
                picked = []
        seen, unique = set(), []
        for item in picked:
            if item[1] not in seen:
                seen.add(item[1])
                unique.append(item)
        collected.append((heading, unique))
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
