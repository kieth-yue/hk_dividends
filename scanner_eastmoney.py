import os
import re
import requests
import pandas as pd
from datetime import datetime, timedelta

FEISHU_WEBHOOK = os.getenv("FEISHU_WEBHOOK")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://gu.qq.com/"
}

def get_dividend_calendar(start_date_str, end_date_str):
    """
    東方財富datacenter接口：分紅日曆
    """
    url = "https://datacenter-web.eastmoney.com/api/data/v1/get"
    page = 1
    records = []
    
    while True:
        params = {
            "sortColumns": "ZS_EX_DIVIDEND_DATE",
            "sortTypes": "1",
            "pageSize": "100",
            "pageNumber": str(page),
            "reportName": "RPT_HK_EXDIVIDEND",
            "columns": "ALL",
            "filter": f"(ZS_EX_DIVIDEND_DATE>='{start_date_str}')(ZS_EX_DIVIDEND_DATE<='{end_date_str}')"
        }
        try:
            resp = requests.get(url, params=params, headers=HEADERS, timeout=10).json()
            if not resp.get("success") or not resp.get("result"):
                break
            data = resp["result"].get("data", [])
            if not data:
                break
            records.extend(data)
            if page >= resp["result"].get("pages", 1):
                break
            page += 1
        except Exception as e:
            print(f"獲取分紅數據失敗: {e}")
            break
    
    return records

def get_stock_snapshot_tencent(sec_code):
    """
    騰訊財經公開接口：實時行情
    """
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
        if len(fields) < 50:
            return None
        
        last_price = float(fields[3]) if fields[3] else 0.0
        
        market_cap = 0.0
        if len(fields) > 45 and fields[45]:
            try:
                market_cap = float(fields[45]) * 100_000_000
            except Exception:
                pass
        
        lot_size = 0
        if len(fields) > 48 and fields[48]:
            try:
                lot_size = int(fields[48])
            except Exception:
                pass
        
        if last_price <= 0:
            return None
        
        return {
            "last_price": last_price,
            "market_cap": market_cap,
            "lot_size": lot_size
        }
    except Exception as e:
        print(f"  騰訊行情請求失敗 {sec_code}: {e}")
        return None

def get_20d_avg_turnover_tencent(sec_code):
    """
    騰訊財經公開K線接口：20日均成交額（港元）
    """
    tencent_code = f"hk{sec_code}"
    # 唔帶qfq，直接拿原始日K線
    url = f"https://web.ifzq.gtimg.cn/appstock/app/kline/kline?param={tencent_code},day,,,20,"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=10).json()
        stock_data = resp.get("data", {}).get(tencent_code, {})
        
        # 依次嘗試多個可能的字段名
        klines = None
        for key in ["day", "hkday", "qfqday", "kline"]:
            if key in stock_data and stock_data[key]:
                klines = stock_data[key]
                break
        
        if not klines:
            # 試下備用域名
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
                    # 騰訊日K線格式：[日期, 開盤, 收盤, 最高, 最低, 成交量, 成交額, ...]
                    volume = float(k[5]) if k[5] else 0
                    close = float(k[2]) if k[2] else 0
                    
                    # 如果有第7個字段成交額，直接用
                    if len(k) >= 7 and k[6]:
                        turnover = float(k[6])
                        if turnover > 10000:  # 成交額正常應該大於1萬
                            turnovers.append(turnover)
                            continue
                    
                    # 否則用成交量×收盤價近似
                    if volume > 0 and close > 0:
                        turnovers.append(volume * close)
            except Exception:
                continue
        
        if not turnovers:
            return 0.0
        return sum(turnovers) / len(turnovers)
    except Exception:
        return 0.0

def get_annual_dividend(sec_code):
    """
    東方財富datacenter接口：過去12個月總派息
    """
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

def push_to_feishu_card(df, start_date, end_date):
    """飛書卡片推送"""
    if not FEISHU_WEBHOOK:
        print("未設定 FEISHU_WEBHOOK，略過推送。")
        return
    if df.empty:
        card = {
            "config": {"wide_screen_mode": True},
            "header": {
                "title": {"tag": "plain_text", "content": "📅 港股除淨派息監控（未來7日）"},
                "template": "blue"
            },
            "elements": [
                {
                    "tag": "div",
                    "text": {"tag": "lark_md", "content": f"掃描區間：`{start_date}` 至 `{end_date}`\n\n**當前條件下暫無符合標準的港股標的。**"}
                }
            ]
        }
    else:
        elements = [
            {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": f"掃描區間：`{start_date}` 至 `{end_date}`\n篩選條件：**市值 > 50億** | **20日均額 > 3000萬** | **本次派息收益率 > 3%**\n排序規則：**本次派息收益率由高至低**\n數據源：東方財富 + 騰訊財經"
                }
            },
            {"tag": "hr"}
        ]
        for _, row in df.iterrows():
            item_md = (
                f"**{row['name']} ({row['code']})**\n"
                f"• 💰 **每股派息**：HK$ {row['dividend_per_share']:.4f}\n"
                f"• 📊 **本次派息收益率**：<font color='green'>**{row['yield_pct']:.2f}%**</font>\n"
                f"• 📈 **年度週息率**：{row['annual_yield_pct']:.2f}%\n"
                f"• 💵 **每手派息**：HK$ {row['dividend_per_lot']:,.2f} (每手 {row['lot_size']:,} 股)\n"
                f"• 📉 **最新股價**：HK$ {row['last_price']:.2f} | **市值**：{row['market_cap_billion']:.2f} 億港元\n"
                f"• 📅 **除淨日**：{row['ex_date']}"
            )
            elements.append({"tag": "div", "text": {"tag": "lark_md", "content": item_md}})
            elements.append({"tag": "hr"})
        card = {
            "config": {"wide_screen_mode": True},
            "header": {
                "title": {"tag": "plain_text", "content": f"🚀 高息港股除淨通知 ({len(df)} 隻標的符合)"},
                "template": "carmine"
            },
            "elements": elements[:-1]
        }
    payload = {"msg_type": "interactive", "card": card}
    try:
        resp = requests.post(FEISHU_WEBHOOK, json=payload, headers={"Content-Type": "application/json"}, timeout=10)
        print("飛書推播回執:", resp.text)
    except Exception as e:
        print(f"飛書推送失敗: {e}")

def main():
    today = datetime.now()
    start_date = today.strftime("%Y-%m-%d")
    end_date = (today + timedelta(days=7)).strftime("%Y-%m-%d")
    
    print(f"開始掃描除淨區間: {start_date} -> {end_date}")
    
    # 1. 東方財富：分紅日曆
    div_records = get_dividend_calendar(start_date, end_date)
    print(f"東方財富返回 {len(div_records)} 條派息記錄")
    
    if not div_records:
        print("該區間內未獲取到分紅記錄。")
        push_to_feishu_card(pd.DataFrame(), start_date, end_date)
        return
    
    results = []
    snap_success = 0
    snap_fail = 0
    turnover_success = 0
    
    # 2. 遍歷處理
    for idx, item in enumerate(div_records):
        raw_code = str(item.get("SECURITY_CODE", "")).zfill(5)
        name = item.get("SECURITY_NAME_ABBR", "")
        ex_date = item.get("ZS_EX_DIVIDEND_DATE", "")[:10]
        
        dividend_hkd = item.get("DPS_HKD", 0.0)
        if not dividend_hkd or float(dividend_hkd) <= 0:
            continue
        dividend_hkd = float(dividend_hkd)
        
        # 騰訊行情
        snap = get_stock_snapshot_tencent(raw_code)
        if not snap:
            snap_fail += 1
            continue
        
        snap_success += 1
        
        # 騰訊K線成交額
        avg_turnover = get_20d_avg_turnover_tencent(raw_code)
        if avg_turnover > 0:
            turnover_success += 1
            
        market_cap = snap["market_cap"]
        last_price = snap["last_price"]
        lot_size = snap["lot_size"]
        
        # 條件1: 市值 > 50億港元
        if market_cap < 5_000_000_000:
            continue
            
        if last_price <= 0 or lot_size <= 0:
            continue
            
        # 條件2: 本次派息收益率 > 3%
        yield_pct = (dividend_hkd / last_price) * 100.0
        if yield_pct < 3.0:
            continue
            
        # 條件3: 20日均成交額 > 3000萬港元（如果攞唔到成交額就暫時放行，避免漏網）
        if avg_turnover > 0 and avg_turnover < 30_000_000:
            continue
        
        # 年度週息率
        annual_total_div = get_annual_dividend(raw_code)
        annual_yield_pct = (annual_total_div / last_price) * 100.0 if annual_total_div > 0 else 0.0
            
        dividend_per_lot = dividend_hkd * lot_size
        
        results.append({
            "name": name,
            "code": raw_code,
            "ex_date": ex_date,
            "dividend_per_share": dividend_hkd,
            "lot_size": lot_size,
            "dividend_per_lot": dividend_per_lot,
            "yield_pct": yield_pct,
            "annual_yield_pct": annual_yield_pct,
            "last_price": last_price,
            "market_cap_billion": market_cap / 100_000_000.0
        })
    
    print(f"\n===== 掃描統計 =====")
    print(f"行情快照成功: {snap_success} 隻，失敗: {snap_fail} 隻")
    print(f"K線成交額成功: {turnover_success} 隻")
    print(f"符合所有篩選條件: {len(results)} 隻")
    
    df = pd.DataFrame(results)
    
    if not df.empty:
        df = df.drop_duplicates(subset=["code"])
        df = df.sort_values(by="yield_pct", ascending=False)
        print("\n===== 篩選結果 =====")
        print(df[["code", "name", "dividend_per_share", "yield_pct", "dividend_per_lot"]].to_string(index=False))
    
    push_to_feishu_card(df, start_date, end_date)

if __name__ == "__main__":
    main()
