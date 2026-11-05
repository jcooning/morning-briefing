"""아침 브리핑 생성기 - briefing.txt 를 새로 씁니다."""
import http.cookiejar
import json
import urllib.request
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
        if i in (4, len(MARKET)):
            lines.append("")  # 지수 / 환율 / 종목 사이 빈 줄
        try:
            series = closes[code].dropna()
            last, prev = float(series.iloc[-1]), float(series.iloc[-2])
            lines.append(f"{name} {fmt_price(last, unit)} ({fmt_change(last, prev)})")
        except Exception:
            lines.append(f"{name} 조회 실패")
    return "\n".join(lines)


def fetch_koreagold():
    """한국금거래소 시세 데이터(JSON)를 가져옵니다."""
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/130 Safari/537.36"
    opener.open(urllib.request.Request("https://www.koreagoldx.co.kr/", headers={"User-Agent": ua}), timeout=20)
    req = urllib.request.Request(
        "https://www.koreagoldx.co.kr/api/main",
        data=b"",
        method="POST",
        headers={
            "User-Agent": ua,
            "X-Requested-With": "XMLHttpRequest",
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "Referer": "https://www.koreagoldx.co.kr/",
        },
    )
    with opener.open(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


def won_diff(value):
    if value > 0:
        return f"▲{value:,.0f}"
    if value < 0:
        return f"▼{abs(value):,.0f}"
    return "-"


def gold_section(data=None):
    data = data or fetch_koreagold()
    p = data["officialPrice4"]
    posted = datetime.strptime(p["date"], "%Y-%m-%d %H:%M:%S")

    def row(name, buy_key, sell_key):
        sell = f"팔때 {p['p_' + sell_key]:,.0f}원({won_diff(p['turm_p_' + sell_key])})"
        if buy_key is None:
            return f"{name} {sell}"
        buy = f"살때 {p['s_' + buy_key]:,.0f}원({won_diff(p['turm_s_' + buy_key])})"
        return f"{name} {buy} / {sell}"

    lines = [
        f"🥇 금시세 (한국금거래소 · 3.75g · {posted.month}/{posted.day} 고시)",
        row("순금", "pure", "pure"),
        row("18K", None, "18k"),
        row("14K", None, "14k"),
        row("백금", "white", "white"),
        row("은", "silver", "silver"),
    ]
    au = next((m for m in data.get("marketPriceList", []) if m.get("type") == "au"), None)
    if au:
        pct = au["askDiffPer"]
        arrow = "▲" if pct > 0 else ("▼" if pct < 0 else "-")
        lines.append(f"국제 금 ${au['ask']:,.2f}/oz ({arrow}{abs(pct):.1f}%)")
    return "\n".join(lines)


def main():
    now = datetime.now(KST)
    header = f"☀ {now.month}/{now.day}({WEEKDAYS[now.weekday()]}) 아침 브리핑"

    sections = [header]
    try:
        sections.append(market_section())
    except Exception as e:
        sections.append(f"📈 증시\n조회 실패 ({type(e).__name__})")
    try:
        sections.append(gold_section())
    except Exception as e:
        sections.append(f"🥇 금시세\n조회 실패 ({type(e).__name__})")

    with open("briefing.txt", "w", encoding="utf-8") as f:
        f.write("\n\n".join(sections) + "\n")
    print("\n\n".join(sections))


if __name__ == "__main__":
    main()
