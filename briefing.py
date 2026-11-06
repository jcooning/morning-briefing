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
        found.sort(reverse=True)  # 숫자가 큰(최신) 모델부터
        models += [n for n in found if n not in models]
    except Exception as e:
        print(f"[Gemini 모델 목록 실패] {e}")
    return models[:6]


def ai_summarize(collected, mbc):
    """Gemini가 분야별로 중요한 기사를 골라 짧게 요약합니다."""
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        raise RuntimeError("GEMINI_API_KEY 없음")

    material = []
    if mbc:
        material.append("## MBC 최신 기사 (분야 구분 없음 — 알맞은 분야에 배치)")
        for j, (source, title, desc) in enumerate(mbc):
            material.append(f"[M{j}] ({source}) {title} — {desc}")
    for i, (heading, items) in enumerate(collected):
        material.append(f"## 분야 {i}: {heading}")
        for j, (source, title, desc) in enumerate(items):
            material.append(f"[{j}] ({source}) {title} — {desc}")
    prompt = (
        "너는 아침 뉴스 브리핑 편집자다. 아래 분야별 기사 목록에서, 분야마다 "
        f"오늘 아침 꼭 알아야 할 중요한 기사 {NEWS_PER_SECTION}개를 골라라.\n"
        "- MBC 최신 기사 목록은 정치·경제·부동산 분야에만 쓴다. 해당 분야의 중요한 MBC 기사가 있으면 우선으로 넣고, 부족하면 그 분야 목록의 다른 언론사 기사로 채운다. IT 분야는 IT 목록에서만 고른다. 한 기사는 한 분야에만 쓴다.\n"
        "- 분야 기준을 엄격히 지킨다. 정치=국회·정당·대통령·정부 인사·외교·북한. 경제=증시·금리·환율·물가·기업 실적·산업·무역. 부동산=집값·청약·재건축·부동산 정책과 대출 규제. IT=AI·반도체·통신·플랫폼·과학기술(IT 매체 기사라도 증시 시황·금융 정책·법원 판결은 IT에 넣지 않는다). 사건사고·연예·스포츠·날씨는 어느 분야에도 넣지 않는다.\n"
        "- 광고성·가벼운 화제·같은 사건의 중복 기사는 제외한다.\n"
        "- 각 기사를 사실 위주로 한국어 1~2문장(90자 이내)으로 요약한다. 제목을 그대로 옮기지 말고 핵심 내용을 풀어 쓴다.\n"
        "- 주어진 제목과 설명에 없는 내용은 추측해서 쓰지 않는다. 의견이나 평가는 덧붙이지 않는다.\n"
        "- 출처는 목록에 적힌 그대로 쓴다.\n"
        "- 분야에 기사가 없으면 items를 빈 배열로 둔다.\n"
        f"- sections에는 분야 0부터 {len(collected) - 1}까지 모든 분야를 빠짐없이 넣는다.\n"
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

    sections = result.get("sections", [])
    by_index = {s.get("index"): s.get("items", []) for s in sections}
    blocks = []
    for i, (heading, items) in enumerate(collected):
        picked = by_index.get(i)
        if picked is None and i < len(sections):
            picked = sections[i].get("items", [])  # 번호가 빠졌으면 순서대로
        lines = [heading]
        for it in (picked or [])[:NEWS_PER_SECTION]:
            if it.get("summary"):
                lines.append(f"· {it['summary']}")
        if len(lines) == 1:  # AI가 이 분야를 빠뜨리면 제목이라도
            lines += [f"· {t}" for s, t, _ in items[:NEWS_PER_SECTION]] or ["· 가져오지 못했어요"]
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def headlines_only(collected):
    """AI 요약이 실패하면 제목만이라도 보여줍니다."""
    blocks = []
    for heading, items in collected:
        lines = [heading] + [f"· {t}" for s, t, _ in items[:NEWS_PER_SECTION]]
        if len(lines) == 1:
            lines.append("· 가져오지 못했어요")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def news_section():
    collected = collect_news()
    mbc = collect_mbc()
    note = "" if mbc else "\n(MBC 기사를 가져오지 못했어요)"
    try:
        return ai_summarize(collected, mbc) + note
    except Exception as e:
        return headlines_only(collected) + note + f"\n(AI 요약 실패: {e})"


# ---------------- 서울 청약 ----------------
# 장기전세·행복주택: 서울주거포털(housing.seoul.go.kr)의 SH·LH 임대 공고 목록
PORTAL = "https://housing.seoul.go.kr/site/main"
LEASE_PAGES = [
    f"{PORTAL}/sh/publicLease/03/list",   # SH 장기전세주택 (미리내집 포함)
    f"{PORTAL}/sh/publicLease/07/list",   # SH 행복주택
    f"{PORTAL}/sh/publicLease/list",      # SH 전체 최신
    f"{PORTAL}/lh/publicLease/list?cp=1&supplyType=publicLease&cnpCdNm=" + urllib.parse.quote("서울특별시") + "&aisTpCd=10",  # LH 서울 행복주택
    f"{PORTAL}/lh/publicLease/list?cp=1&supplyType=publicLease&cnpCdNm=" + urllib.parse.quote("서울특별시") + "&aisTpCd=42",  # LH 서울 신혼희망 행복주택
]
LEASE_TYPES = ["장기전세", "행복주택"]
OPEN_WORDS = ("모집중", "접수중", "모집예정", "접수예정", "공고중", "상담요청")


def find_col(headers, *names):
    for i, h in enumerate(headers):
        if any(n in h for n in names):
            return i
    return None


def lease_notices():
    """[(유형, 공고명, 공고일, 마감일, 상태), ...] — 모집 중인 장기전세·행복주택"""
    found, seen = [], set()
    rows_read = 0
    for url in LEASE_PAGES:
        try:
            page = get_html(url)
        except Exception as e:
            print(f"[청약 목록] {url} 실패: {type(e).__name__}")
            continue
        headers = [clean(h) for h in re.findall(r"<th[^>]*>(.*?)</th>", page, re.S)]
        c_kind = find_col(headers, "유형")
        c_title = find_col(headers, "공고명")
        c_posted = find_col(headers, "게시일", "공고일")
        c_end = find_col(headers, "마감일")
        c_status = find_col(headers, "상태")
        if None in (c_kind, c_title, c_posted, c_status):
            print(f"[청약 목록] {url} 표 머리글을 못 찾음: {headers}")
            continue
        for row in re.findall(r"<tr[^>]*>(.*?)</tr>", page, re.S):
            cells = [clean(c) for c in re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)]
            if len(cells) <= max(c_kind, c_title, c_posted, c_status):
                continue
            rows_read += 1
            kind, title, status = cells[c_kind], cells[c_title], cells[c_status]
            label = next((t for t in LEASE_TYPES if t in kind), None)
            if not label or title in seen or not any(w in status for w in OPEN_WORDS):
                continue
            seen.add(title)
            end = cells[c_end] if c_end is not None and c_end < len(cells) else ""
            found.append((label, title, cells[c_posted][:10], end[:10], status))
    print(f"[청약 목록] 읽은 공고 {rows_read}건")
    if rows_read == 0:
        raise RuntimeError("공고 목록을 하나도 읽지 못함")  # '없음'과 '실패'를 구분
    return found


def apt_notices():
    """청약홈(공공데이터포털) — 서울 아파트 일반 분양 중 접수가 안 끝난 것"""
    key = os.environ.get("DATA_GO_KR_KEY")
    if not key:
        return None
    today = datetime.now(KST).date()
    params = urllib.parse.urlencode({
        "page": 1, "perPage": 50, "serviceKey": key,
        "cond[SUBSCRPT_AREA_CODE_NM::EQ]": "서울",
        "cond[RCRIT_PBLANC_DE::GTE]": (today - timedelta(days=60)).isoformat(),
    })
    url = "https://api.odcloud.kr/api/ApplyhomeInfoDetailSvc/v1/getAPTLttotPblancDetail?" + params
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=20) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    if "data" not in payload:
        raise RuntimeError(str(payload)[:200])
    rows = payload["data"]
    result = []
    for r in rows:
        end = r.get("RCEPT_ENDDE") or ""
        if end and end < today.isoformat():
            continue
        result.append((r.get("RCEPT_BGNDE") or "", r.get("HOUSE_NM", ""), end, r.get("TOT_SUPLY_HSHLDCO")))
    return sorted(result)


def short_date(iso):
    try:
        d = datetime.strptime(iso[:10], "%Y-%m-%d")
        return f"{d.month}/{d.day}"
    except ValueError:
        return iso


def cheongyak_section():
    lines = ["🏢 서울 청약"]
    try:
        apts = apt_notices()
        if apts is None:
            pass  # 키가 없으면 일반 분양은 건너뜀
        elif apts:
            for start, name, end, units in apts[:5]:
                unit = f" · {units}세대" if units else ""
                lines.append(f"[일반] {name} · 접수 {short_date(start)}~{short_date(end)}{unit}")
        else:
            lines.append("[일반] 접수 예정·진행 중인 단지 없음")
    except Exception as e:
        print(f"[청약 일반] 실패: {e}")
        lines.append("[일반] 조회 실패")
    try:
        leases = lease_notices()
        for label in LEASE_TYPES:
            mine = [x for x in leases if x[0] == label]
            for _, title, posted, end, status in mine[:3]:
                until = f" · ~{short_date(end)} 마감" if end else ""
                lines.append(f"[{label}] {title} · 공고 {short_date(posted)}{until} · {status}")
            if not mine:
                lines.append(f"[{label}] 모집 중인 공고 없음")
    except Exception as e:
        print(f"[청약 임대] 실패: {e}")
        lines.append("[장기전세·행복주택] 조회 실패")
    return "\n".join(lines)


def gold_section():
    """국제 금 시세 (뉴욕 금 선물, 달러/온스)"""
    series = yf.download(["GC=F"], period="10d", progress=False, auto_adjust=False)["Close"]["GC=F"].dropna()
    last, prev = float(series.iloc[-1]), float(series.iloc[-2])
    return f"🥇 금시세\n국제 금 ${last:,.2f}/oz ({fmt_change(last, prev)})"


def main():
    now = datetime.now(KST)
    header = f"☀ {now.month}/{now.day}({WEEKDAYS[now.weekday()]}) 아침 브리핑"
    try:
        days = get_weather()
    except Exception as e:
        print(f"[날씨] 실패: {e}")
        days = None

    # briefing.txt — 제목·날씨·증시·뉴스·청약·국제 금 (이 뒤에 아이폰이 순금 시세를 붙임)
    top = header + "\n" + (today_weather(days) if days else "날씨 조회 실패")
    sections = [top]
    try:
        sections.append(market_section())
    except Exception as e:
        sections.append(f"📈 증시\n조회 실패 ({type(e).__name__})")
    try:
        sections.append(news_section())
    except Exception as e:
        sections.append(f"📰 뉴스\n조회 실패 ({type(e).__name__})")
    try:
        sections.append(cheongyak_section())
    except Exception as e:
        sections.append(f"🏢 서울 청약\n조회 실패 ({type(e).__name__})")
    try:
        sections.append(gold_section())
    except Exception as e:
        sections.append(f"🥇 금시세\n국제 금 조회 실패 ({type(e).__name__})")
    with open("briefing.txt", "w", encoding="utf-8") as f:
        f.write("\n\n".join(sections) + "\n")

    # briefing2.txt — (금요일) 주말 날씨 + 오늘의 말씀 (메시지 맨 아래)
    bottom = []
    if now.weekday() == 4 and days:
        bottom.append(weekend_weather(days))
    try:
        bottom.append(bible_section())
    except Exception as e:
        print(f"[성경] 실패: {e}")
    with open("briefing2.txt", "w", encoding="utf-8") as f:
        f.write("\n\n".join(bottom) + "\n")

    print("\n\n".join(sections))
    print("=" * 20)
    print("\n\n".join(bottom))


if __name__ == "__main__":
    main()
