import os
import json
import math
import requests
import pandas as pd
from datetime import datetime, timedelta

FEISHU_WEBHOOK = os.getenv("FEISHU_WEBHOOK")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Referer": "https://data.eastmoney.com/"
}

def get_exchange_rates():
    """獲取最新匯率（RMB/USD 兌 HKD），預設兜底值"""
    rates = {"HKD": 1.0, "CNY": 1.08, "RMB": 1.08, "USD": 7.82}
    try:
        url = "https://push2.eastmoney.com/api/qt/clist/get?pn=1&pz=10&po=1&np=1&fltt=2&invt=2&fid=f3&fs=m:119"
        res = requests.get(url, headers=HEADERS, timeout=5).json()
        for item in res.get("data", {}).get("diff", []):
            if item.get("f14") == "美元兌港元":
                rates["USD"] = float(item["f2"])
            elif item.get("f14") == "離岸人民幣兌港元":
                rates["CNY"] = rates["RMB"] = float(item["f2"])
    except Exception:
        pass
    return rates

def get_dividend_calendar(start_date_str, end_date_str):
    """
    從東方財富獲取指定日期範圍內除淨的港股分紅數據
    """
    url = "https://datacenter-web.eastmoney.com/api/data/v1/get"
    page = 1
    records = []
    
    while True:
        params = {
            "sortColumns": "EX_DIVIDEND_DATE",
            "sortTypes": "1",
            "pageSize": "50",
            "pageNumber": str(page),
            "reportName": "RPT_HK_DIVIDEND",
            "columns": "ALL",
            "filter": f"(EX_DIVIDEND_DATE>='{start_date_str}')(EX_DIVIDEND_DATE<='{end_date_str}')"
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

def get_annual_dividend(sec_code, rates):
    """
    獲取過去12個月的總派息（港元），用於計算年度週息率
    """
    url = "https://datacenter-web.eastmoney.com/api/data/v1/get"
    one_year_ago = (datetime.now() - timedelta(days=365)).strftime("%Y-%m-%d")
    today = datetime.now().strftime("%Y-%m-%d")
    
    total_div = 0.0
    page = 1
    
    while True:
        params = {
            "sortColumns": "EX_DIVIDEND_DATE",
            "sortTypes": "-1",
            "pageSize": "50",
            "pageNumber": str(page),
            "reportName": "RPT_HK_DIVIDEND",
            "columns": "ALL",
            "filter": f"(SECURITY_CODE=\"{sec_code}\")(EX_DIVIDEND_DATE>='{one_year_ago}')(EX_DIVIDEND_DATE<='{today}')"
        }
        try:
            resp = requests.get(url, params=params, headers=HEADERS, timeout=10).json()
            if not resp.get("success") or not resp.get("result"):
                break
            
            data = resp["result"].get("data", [])
            if not data:
                break
            
            for item in data:
                per_share = item.get("DIVIDEND_PER_SHARE", 0.0)
                currency = str(item.get("CURRENCY", "HKD")).upper()
                if per_share and float(per_share) > 0:
                    total_div += float(per_share) * rates.get(currency, 1.0)
            
            if page >= resp["result"].get("pages", 1):
                break
            page += 1
        except Exception:
            break
    
    return total_div

def get_stock_snapshot(sec_code):
    """
    獲取單隻港股的即時行情（最新價、市值、每手股數）
    sec_code 格式: 5位代碼，例如 '00700'
    """
    sec_id = f"116.{sec_code}"
    url = f"https://push2.eastmoney.com/api/qt/stock/get?secid={sec_id}&fields=f43,f44,f45,f46,f47,f48,f57,f58,f116,f162"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=5).json()
        data = resp.get("data")
        if not data:
            return None
        
        # 字段定義:
        # f43: 最新價 (需要 / 1000)
        # f57: 代碼, f58: 名稱
        # f116: 總市值 (港元)
        # f162: 每手股數
        last_price = data.get("f43", 0) / 1000.0 if data.get("f43") != "-" else 0.0
        market_cap = data.get("f116", 0)
        lot_size = data.get("f162", 0)
        
        return {
            "last_price": last_price,
            "market_cap": float(market_cap) if market_cap != "-" else 0.0,
            "lot_size": int(lot_size) if lot_size != "-" else 0
        }
    except Exception:
        return None

def get_20d_avg_turnover(sec_code):
    """
    獲取過去 20 個交易日的平均成交額（港元）
    """
    sec_id = f"116.{sec_code}"
    url = f"https://push2his.eastmoney.com/api/qt/stock/kline/get?secid={sec_id}&klt=101&fqt=1&lmt=20&end=20500101&fields1=f1,f2,f3&fields2=f51,f56"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=5).json()
        klines = resp.get("data", {}).get("klines", [])
        if not klines:
            return 0.0
        
        # 每條 kline: '日期,成交額'
        turnovers = [float(k.split(",")[1]) for k in klines]
        return sum(turnovers) / len(turnovers)
    except Exception:
        return 0.0

def push_to_feishu_card(df, start_date, end_date):
    """將結果格式化為飛書富文本卡片發送"""
    if not FEISHU_WEBHOOK:
        print("未設定 FEISHU_WEBHOOK，略過推送。")
        return
    if df.empty:
        card = {
            "config": {"wide_screen_mode": True},
            "header": {
                "title": {"tag": "plain_text", "content": "📅 港股除淨派息監控（未來5日）"},
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
                    "content": f"掃描區間：`{start_date}` 至 `{end_date}`\n篩選條件：**市值 > 50億** | **20日均額 > 3000萬** | **本次派息收益率 > 3%**\n排序規則：**本次派息收益率由高至低**"
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
                f"• 📅 **除淨日**：{row['ex_date']} | **派息日**：{row['pay_date']}"
            )
            elements.append({"tag": "div", "text": {"tag": "lark_md", "content": item_md}})
            elements.append({"tag": "hr"})
        card = {
            "config": {"wide_screen_mode": True},
            "header": {
                "title": {"tag": "plain_text", "content": f"🚀 高息港股除淨通知 ({len(df)} 隻標的符合)"},
                "template": "carmine"
            },
            "elements": elements[:-1] # 移除最後一條 hr
        }
    payload = {"msg_type": "interactive", "card": card}
    resp = requests.post(FEISHU_WEBHOOK, json=payload, headers={"Content-Type": "application/json"})
    print("飛書推播回執:", resp.text)

def main():
    # 1. 計算日期區間（今天起至未來第 5 天）
    today = datetime.now()
    start_date = today.strftime("%Y-%m-%d")
    end_date = (today + timedelta(days=5)).strftime("%Y-%m-%d")
    
    print(f"開始掃描除淨區間: {start_date} -> {end_date}")
    rates = get_exchange_rates()
    div_records = get_dividend_calendar(start_date, end_date)
    
    if not div_records:
        print("該區間內未獲取到分紅記錄。")
        push_to_feishu_card(pd.DataFrame(), start_date, end_date)
        return
    
    results = []
    
    # 2. 遍歷每檔股票並進行多維度過濾
    for item in div_records:
        raw_code = str(item.get("SECURITY_CODE", "")).zfill(5)
        name = item.get("SECURITY_NAME_ABBR", "")
        ex_date = item.get("EX_DIVIDEND_DATE", "")[:10]
        pay_date = item.get("PAYMENT_DATE", "")
        pay_date = pay_date[:10] if pay_date else "待定"
        
        # 每股分紅派息數額與貨幣
        per_share_div = item.get("DIVIDEND_PER_SHARE", 0.0)
        currency = str(item.get("CURRENCY", "HKD")).upper()
        
        if not per_share_div or float(per_share_div) <= 0:
            continue
            
        rate = rates.get(currency, 1.0)
        dividend_hkd = float(per_share_div) * rate
        
        # 獲取即時快照數據
        snap = get_stock_snapshot(raw_code)
        if not snap:
            continue
            
        market_cap = snap["market_cap"]
        last_price = snap["last_price"]
        lot_size = snap["lot_size"]
        
        # 條件 1: 市值 > 50 億港元
        if market_cap < 5_000_000_000:
            continue
            
        if last_price <= 0 or lot_size <= 0:
            continue
            
        # 條件 2: 本次派息收益率 = (每股派息 / 最新價) * 100% > 3%
        yield_pct = (dividend_hkd / last_price) * 100.0
        if yield_pct < 3.0:
            continue
            
        # 條件 3: 20 日均成交額 > 3,000 萬港元
        avg_turnover = get_20d_avg_turnover(raw_code)
        if avg_turnover < 30_000_000:
            continue
        
        # 計算年度週息率（過去12個月總派息/最新價）
        annual_total_div = get_annual_dividend(raw_code, rates)
        annual_yield_pct = (annual_total_div / last_price) * 100.0 if annual_total_div > 0 else 0.0
            
        dividend_per_lot = dividend_hkd * lot_size
        
        results.append({
            "name": name,
            "code": raw_code,
            "ex_date": ex_date,
            "pay_date": pay_date,
            "dividend_per_share": dividend_hkd,
            "lot_size": lot_size,
            "dividend_per_lot": dividend_per_lot,
            "yield_pct": yield_pct,
            "annual_yield_pct": annual_yield_pct,
            "last_price": last_price,
            "market_cap_billion": market_cap / 100_000_000.0
        })
    
    df = pd.DataFrame(results)
    
    if not df.empty:
        # 按本次派息收益率由高到低排序
        df = df.sort_values(by="yield_pct", ascending=False)
        print("\n篩選結果:")
        print(df[["code", "name", "dividend_per_share", "yield_pct", "annual_yield_pct", "dividend_per_lot"]])
    
    # 3. 發送至飛書
    push_to_feishu_card(df, start_date, end_date)

if __name__ == "__main__":
    main()
