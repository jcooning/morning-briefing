"""아침 브리핑 생성기 - briefing.txt 를 새로 씁니다."""
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
        sections.append(gold_section())
    except Exception as e:
        sections.append(f"🥇 금시세\n국제 금 조회 실패 ({type(e).__name__})")

    with open("briefing.txt", "w", encoding="utf-8") as f:
        f.write("\n\n".join(sections) + "\n")
    print("\n\n".join(sections))


if __name__ == "__main__":
    main()
