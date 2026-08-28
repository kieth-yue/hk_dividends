import os
import re
import requests
import pandas as pd
from datetime import datetime, timedelta
from bs4 import BeautifulSoup

FEISHU_WEBHOOK = os.getenv("FEISHU_WEBHOOK")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://www.hkexnews.hk/"
}

RATES = {"HKD": 1.0, "CNY": 1.08, "RMB": 1.08, "USD": 7.82,
         "港幣": 1.0, "港元": 1.0, "人民幣": 1.08, "美元": 7.82}

# ETF/衍生品關鍵詞（已移除會誤殺正股嘅發行商名稱：國泰/恒生/中銀/滙豐/渣打/海通/工銀/建銀/農銀）
EXCLUDE_KEYWORDS = ["兌", "債", "ETF", "Ｒ", "－Ｒ", "-R", "－Ｕ", "-U",
                    "牛", "熊", "窩輪", "認股證", "界內證", "槓桿", "反向",
                    "ＧＸ", "GX", "安碩", "領航", "貝萊德", "iShares",
                    "未來資產", "三星", "法興", "瑞通"]

def normalize_name(name):
    name = name.replace("　", " ").strip()
    result = ""
    for ch in name:
        code = ord(ch)
        if 0xFF01 <= code <= 0xFF5E:
            result += chr(code - 0xFEE0)
        elif code == 0x3000:
            result += " "
        else:
            result += ch
    return result.strip()

def is_excluded_stock(code, name):
    # 8開頭為人民幣櫃台，9開頭為美元櫃台，0開頭為標準正股（含09xxx）
    if code.startswith("8") or code.startswith("9"):
        return True
    for kw in EXCLUDE_KEYWORDS:
        if kw in name:
            return True
    return False

def parse_dividend_amount(text):
    """
    從港交所公告文本中解析每股派息金額（港元）
    支援：港仙（自動/100）、港元/港幣、美元、人民幣
    規則：
    1. 按匹配位置去重，避免同一金額被多個pattern重複匹配
    2. 如果搵到港元/港幣/港仙金額，就唔再計外幣（避免港元+美元等值重複）
    3. 只有冇港元金額時，先至用美元/人民幣換算
    """
    text = text.replace(" ", "").replace("　", "")

    hkd_patterns = [
        # 港仙
        (r"中期股息([\d.]+)港仙", 0.01, True),
        (r"中期息([\d.]+)港仙", 0.01, True),
        (r"末期股息([\d.]+)港仙", 0.01, True),
        (r"末期息([\d.]+)港仙", 0.01, True),
        (r"特別股息([\d.]+)港仙", 0.01, True),
        (r"特別息([\d.]+)港仙", 0.01, True),
        (r"現金股息每股([\d.]+)港仙", 0.01, True),
        (r"每股派息([\d.]+)港仙", 0.01, True),
        (r"每股派([\d.]+)港仙", 0.01, True),
        (r"股息每股([\d.]+)港仙", 0.01, True),
        (r"派息每股([\d.]+)港仙", 0.01, True),
        (r"每股([\d.]+)港仙", 0.01, True),
        # 港元
        (r"中期股息([\d.]+)港元", 1.0, False),
        (r"中期息([\d.]+)港元", 1.0, False),
        (r"末期股息([\d.]+)港元", 1.0, False),
        (r"末期息([\d.]+)港元", 1.0, False),
        (r"特別股息([\d.]+)港元", 1.0, False),
        (r"特別息([\d.]+)港元", 1.0, False),
        (r"現金股息每股([\d.]+)港元", 1.0, False),
        (r"每股派息([\d.]+)港元", 1.0, False),
        (r"每股派([\d.]+)港元", 1.0, False),
        (r"股息每股([\d.]+)港元", 1.0, False),
        (r"派息每股([\d.]+)港元", 1.0, False),
        (r"每股([\d.]+)港元", 1.0, False),
        # 港幣
        (r"中期股息([\d.]+)港幣", 1.0, False),
        (r"中期息([\d.]+)港幣", 1.0, False),
        (r"末期股息([\d.]+)港幣", 1.0, False),
        (r"末期息([\d.]+)港幣", 1.0, False),
        (r"特別股息([\d.]+)港幣", 1.0, False),
        (r"特別息([\d.]+)港幣", 1.0, False),
        (r"現金股息每股([\d.]+)港幣", 1.0, False),
        (r"每股派息([\d.]+)港幣", 1.0, False),
        (r"每股派([\d.]+)港幣", 1.0, False),
        (r"股息每股([\d.]+)港幣", 1.0, False),
        (r"派息每股([\d.]+)港幣", 1.0, False),
        (r"每股([\d.]+)港幣", 1.0, False),
    ]

    fx_patterns = [
        # 美元
        (r"中期股息([\d.]+)美元", RATES["USD"], False),
        (r"中期息([\d.]+)美元", RATES["USD"], False),
        (r"末期股息([\d.]+)美元", RATES["USD"], False),
        (r"末期息([\d.]+)美元", RATES["USD"], False),
        (r"特別股息([\d.]+)美元", RATES["USD"], False),
        (r"特別息([\d.]+)美元", RATES["USD"], False),
        (r"現金股息每股([\d.]+)美元", RATES["USD"], False),
        (r"每股派息([\d.]+)美元", RATES["USD"], False),
        (r"每股派([\d.]+)美元", RATES["USD"], False),
        (r"股息每股([\d.]+)美元", RATES["USD"], False),
        (r"派息每股([\d.]+)美元", RATES["USD"], False),
        (r"每股([\d.]+)美元", RATES["USD"], False),
        # 人民幣
        (r"中期股息([\d.]+)人民幣", RATES["CNY"], False),
        (r"中期息([\d.]+)人民幣", RATES["CNY"], False),
        (r"末期股息([\d.]+)人民幣", RATES["CNY"], False),
        (r"末期息([\d.]+)人民幣", RATES["CNY"], False),
        (r"特別股息([\d.]+)人民幣", RATES["CNY"], False),
        (r"特別息([\d.]+)人民幣", RATES["CNY"], False),
        (r"現金股息每股([\d.]+)人民幣", RATES["CNY"], False),
        (r"每股派息([\d.]+)人民幣", RATES["CNY"], False),
        (r"每股派([\d.]+)人民幣", RATES["CNY"], False),
        (r"股息每股([\d.]+)人民幣", RATES["CNY"], False),
        (r"派息每股([\d.]+)人民幣", RATES["CNY"], False),
        (r"每股([\d.]+)人民幣", RATES["CNY"], False),
    ]

    def match_patterns(patterns):
        matched_spans = []
        total = 0.0
        for pattern, rate, is_cents in patterns:
            for match in re.finditer(pattern, text):
                amount_str = match.group(1)
                try:
                    amount = float(amount_str)
                    if amount <= 0:
                        continue
                    span = match.span(1)
                    already_matched = False
                    for existing_span in matched_spans:
                        if span[0] < existing_span[1] and span[1] > existing_span[0]:
                            already_matched = True
                            break
                    if not already_matched:
                        matched_spans.append(span)
                        if is_cents:
                            total += amount / 100.0
                        else:
                            total += amount * rate
                except Exception:
                    continue
        return total

    hkd_total = match_patterns(hkd_patterns)
    if hkd_total > 0:
        return hkd_total
    fx_total = match_patterns(fx_patterns)
    if fx_total > 0:
        return fx_total
    return None

def get_dividend_calendar_hkex(start_date_str, end_date_str):
    url = "https://www3.hkexnews.hk/reports/doe/eent_c.htm"
    records = []
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.encoding = "utf-8"
        soup = BeautifulSoup(resp.text, "html.parser")
        tables = soup.find_all("table")
        target_table = None
        for t in tables:
            if "證券簡稱" in t.text and "除淨日" in t.text:
                target_table = t
                break
        if not target_table:
            print("港交所：找不到權益表格")
            return records
        rows = target_table.find_all("tr")
        for row in rows[2:]:
            cols = row.find_all("td")
            if len(cols) < 4:
                continue
            try:
                stock_text = cols[1].text.strip()
                code_match = re.search(r"\((\d{4,5})\)", stock_text)
                if not code_match:
                    continue
                raw_code = code_match.group(1).zfill(5)
                name = normalize_name(stock_text[:code_match.start()].strip())
                if is_excluded_stock(raw_code, name):
                    continue
                content = cols[3].text.strip()
                if "紅股" in content and "息" not in content:
                    continue
                if "供股" in content:
                    continue
                if "實物分派" in content:
                    continue
                ex_date_text = cols[4].text.strip()
                if not ex_date_text or "/" not in ex_date_text:
                    continue
                ex_date_parts = ex_date_text.split("/")
                ex_date = None
                if len(ex_date_parts) == 3:
                    ex_date = datetime.strptime(ex_date_text, "%d/%m/%Y").strftime("%Y-%m-%d")
                elif len(ex_date_parts) == 2:
                    day, month = ex_date_parts
                    year = datetime.now().year
                    ex_date = f"{year}-{int(month):02d}-{int(day):02d}"
                if not ex_date:
                    continue
                if ex_date < start_date_str or ex_date > end_date_str:
                    continue
                div_hkd = parse_dividend_amount(content)
                if not div_hkd or div_hkd <= 0:
                    continue
                records.append({
                    "code": raw_code,
                    "name": name,
                    "ex_date": ex_date,
                    "dividend_per_share": div_hkd,
                    "content": content[:100]
                })
            except Exception:
                continue
    except Exception as e:
        print(f"港交所數據獲取失敗: {e}")
    return records

def get_stock_snapshot_tencent(sec_code):
    tencent_code = f"hk{sec_code}"
    url = f"https://qt.gtimg.cn/q={tencent_code}"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=10)
        resp.encoding = "gbk"
        text = resp.text.strip()
        match = re.search(r'="([^"]+)"', text)
        if not match:
            return None
        fields = match.group(1).split("~")
        if len(fields) < 70:
            return None
        last_price = float(fields[3]) if fields[3] else 0.0
        market_cap = 0.0
        if fields[44]:
            try:
                cap_val = float(fields[44])
                if cap_val > 0:
                    market_cap = cap_val * 100_000_000
            except Exception:
                pass
        if market_cap <= 0 and len(fields) > 69 and fields[69]:
            try:
                total_shares = float(fields[69])
                market_cap = last_price * total_shares
            except Exception:
                pass
        lot_size = 0
        if len(fields) > 60 and fields[60]:
            try:
                lot_size = int(fields[60])
            except Exception:
                pass
        if last_price <= 0:
            return None
        return {
            "last_price": last_price,
            "market_cap": market_cap,
            "lot_size": lot_size
        }
    except Exception:
        return None

def get_20d_avg_turnover_tencent(sec_code):
    tencent_code = f"hk{sec_code}"
    url = f"https://web.ifzq.gtimg.cn/appstock/app/kline/kline?param={tencent_code},day,,,20,"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=10).json()
        stock_data = resp.get("data", {}).get(tencent_code, {})
        klines = None
        for key in ["day", "hkday", "qfqday", "kline"]:
            if key in stock_data and stock_data[key]:
                klines = stock_data[key]
                break
        if not klines:
            url2 = f"https://ifzq.gtimg.cn/appstock/app/hkfqkline/get?param={tencent_code},day,,,20,"
            resp2 = requests.get(url2, headers=HEADERS, timeout=10).json()
            stock_data2 = resp2.get("data", {}).get(tencent_code, {})
            for key in ["day", "hkday", "qfqday"]:
                if key in stock_data2 and stock_data2[key]:
                    klines = stock_data2[key]
                    break
        if not klines or len(klines) < 5:
            return 0.0
        turnovers = []
        for k in klines[-20:]:
            try:
                if len(k) >= 6:
                    volume = float(k[5]) if k[5] else 0
                    close = float(k[2]) if k[2] else 0
                    if len(k) >= 7 and k[6]:
                        turnover = float(k[6])
                        if turnover > 10000:
                            turnovers.append(turnover)
                            continue
                    if volume > 0 and close > 0:
                        turnovers.append(volume * close)
            except Exception:
                continue
        if not turnovers:
            return 0.0
        return sum(turnovers) / len(turnovers)
    except Exception:
        return 0.0

def get_annual_dividend_eastmoney(sec_code):
    url = "https://datacenter-web.eastmoney.com/api/data/v1/get"
    one_year_ago = (datetime.now() - timedelta(days=365)).strftime("%Y-%m-%d")
    today = datetime.now().strftime("%Y-%m-%d")
    total_div = 0.0
    page = 1
    while True:
        params = {
            "sortColumns": "ZS_EX_DIVIDEND_DATE",
            "sortTypes": "-1",
            "pageSize": "100",
            "pageNumber": str(page),
            "reportName": "RPT_HK_EXDIVIDEND",
            "columns": "SECURITY_CODE,DPS_HKD,ZS_EX_DIVIDEND_DATE",
            "filter": f"(SECURITY_CODE=\"{sec_code}\")(ZS_EX_DIVIDEND_DATE>='{one_year_ago}')(ZS_EX_DIVIDEND_DATE<='{today}')"
        }
        try:
            resp = requests.get(url, params=params, headers=HEADERS, timeout=10).json()
            if not resp.get("success") or not resp.get("result"):
                break
            data = resp["result"].get("data", [])
            if not data:
                break
            for item in data:
                dps = item.get("DPS_HKD", 0)
                if dps and float(dps) > 0:
                    total_div += float(dps)
            if page >= resp["result"].get("pages", 1):
                break
            page += 1
        except Exception:
            break
    return total_div

def push_to_feishu_card(df, start_date, end_date, generate_dt):
    if not FEISHU_WEBHOOK:
        print("未設定 FEISHU_WEBHOOK，略過推送。")
        return
    if df.empty:
        card = {
            "config": {"wide_screen_mode": True},
            "header": {
                "title": {"tag": "plain_text", "content": "📅 港股除淨派息監控"},
                "template": "blue"
            },
            "elements": [
                {
                    "tag": "div",
                    "text": {"tag": "lark_md", "content": f"生成時間：`{generate_dt}`\n掃描區間：`{start_date}` 至 `{end_date}`\n\n**當前條件下暫無符合標準港股。**"}
                }
            ]
        }
    else:
        elements = [
            {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": f"生成時間：`{generate_dt}`\n掃描區間：`{start_date}` ~ `{end_date}`\n篩選：市值>50億｜20日均額>3000萬｜派息收益率>3%\n數據源：港交所披露易 + 騰訊財經"
                }
            },
            {"tag": "hr"}
        ]
        for _, row in df.iterrows():
            days_diff = row["days_to_ex"]
            item_md = (
                f"**{row['name']} ({row['code']})**\n"
                f"💵 每手派息：HK$ {row['dividend_per_lot']:,.0f}\n"
                f"💸 每手入場：HK$ {row['lot_cost']:,.0f}（{row['lot_size']:,}股 × ${row['last_price']:.2f}）\n"
                f"📈 收益率：<font color='green'>{row['yield_pct']:.2f}%</font>｜年度週息：{row['annual_yield_pct']:.2f}%\n"
                f"📊 每股派息：HK$ {row['dividend_per_share']:.4f}｜市值：{row['market_cap_billion']:.0f}億\n"
                f"📅 除淨日：{row['ex_date']}｜距除淨：{days_diff}日"
            )
            elements.append({"tag": "div", "text": {"tag": "lark_md", "content": item_md}})
            elements.append({"tag": "hr"})
        card = {
            "config": {"wide_screen_mode": True},
            "header": {
                "title": {"tag": "plain_text", "content": f"🚀 高息港股除淨通知 ({len(df)} 隻標的符合)"},
                "template": "blue"
            },
            "elements": elements[:-1]
        }
    payload = {"msg_type": "interactive", "card": card}
    try:
        resp = requests.post(FEISHU_WEBHOOK, json=payload, headers={"Content-Type": "application/json"}, timeout=10)
        print("飛書推播回執:", resp.text)
    except Exception as e:
        print(f"飛書推送失敗: {e}")

def get_trading_day_range(days=7):
    """計算未來N個交易日（跳過星期六、日）"""
    today = datetime.now().date()
    current = today
    collect = []
    while len(collect) < days:
        wd = current.weekday()
        if wd < 5:  # 0=週一 ... 4=週五
            collect.append(current)
        current = current + timedelta(days=1)
    start = collect[0].strftime("%Y-%m-%d")
    end = collect[-1].strftime("%Y-%m-%d")
    return start, end, today

def main():
    start_date, end_date, today_date = get_trading_day_range(7)
    generate_datetime = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"開始掃描除淨區間(跳週六日): {start_date} -> {end_date}")

    div_records = get_dividend_calendar_hkex(start_date, end_date)
    print(f"港交所披露易返回 {len(div_records)} 條現金派息記錄")

    if not div_records:
        print("該區間內未獲取到分紅記錄。")
        push_to_feishu_card(pd.DataFrame(), start_date, end_date, generate_datetime)
        return

    results = []
    snap_success = 0
    snap_fail = 0
    turnover_success = 0
    filtered_cap = 0
    filtered_yield = 0
    filtered_turnover = 0
    filtered_lot = 0

    for item in div_records:
        raw_code = item["code"]
        name = item["name"]
        ex_date_str = item["ex_date"]
        dividend_hkd = item["dividend_per_share"]

        snap = get_stock_snapshot_tencent(raw_code)
        if not snap:
            snap_fail += 1
            continue
        snap_success += 1

        avg_turnover = get_20d_avg_turnover_tencent(raw_code)
        if avg_turnover > 0:
            turnover_success += 1

        market_cap = snap["market_cap"]
        last_price = snap["last_price"]
        lot_size = snap["lot_size"]

        if market_cap < 5_000_000_000:
            filtered_cap += 1
            continue
        if last_price <= 0:
            continue
        if lot_size <= 0:
            filtered_lot += 1
            continue

        yield_pct = (dividend_hkd / last_price) * 100.0
        if yield_pct < 3.0:
            filtered_yield += 1
            continue
        if avg_turnover > 0 and avg_turnover < 30_000_000:
            filtered_turnover += 1
            continue

        annual_total_div = get_annual_dividend_eastmoney(raw_code)
        annual_yield_pct = (annual_total_div / last_price) * 100.0 if annual_total_div > 0 else 0.0
        dividend_per_lot = dividend_hkd * lot_size
        lot_cost = last_price * lot_size

        ex_date_obj = datetime.strptime(ex_date_str, "%Y-%m-%d").date()
        days_to_ex = (ex_date_obj - today_date).days

        results.append({
            "name": name,
            "code": raw_code,
            "ex_date": ex_date_str,
            "dividend_per_share": dividend_hkd,
            "lot_size": lot_size,
            "dividend_per_lot": dividend_per_lot,
            "lot_cost": lot_cost,
            "yield_pct": yield_pct,
            "annual_yield_pct": annual_yield_pct,
            "last_price": last_price,
            "market_cap_billion": market_cap / 100_000_000.0,
            "days_to_ex": days_to_ex
        })

    print(f"\n===== 掃描統計 =====")
    print(f"行情快照成功: {snap_success} 隻，失敗: {snap_fail} 隻")
    print(f"K線成交額成功: {turnover_success} 隻")
    print(f"市值過濾淘汰: {filtered_cap} 隻")
    print(f"每手股數缺失: {filtered_lot} 隻")
    print(f"收益率過濾淘汰: {filtered_yield} 隻")
    print(f"成交額過濾淘汰: {filtered_turnover} 隻")
    print(f"符合所有篩選條件: {len(results)} 隻")

    df = pd.DataFrame(results)
    if not df.empty:
        df = df.drop_duplicates(subset=["code"])
        df = df.sort_values(by="yield_pct", ascending=False)
        print("\n===== 最終篩選結果 =====")
        print(df[["code", "name", "dividend_per_share", "yield_pct", "dividend_per_lot", "lot_cost", "ex_date", "days_to_ex"]].to_string(index=False))

    push_to_feishu_card(df, start_date, end_date, generate_datetime)

if __name__ == "__main__":
    main()
