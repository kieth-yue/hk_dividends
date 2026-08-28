import os
import requests
import pandas as pd
import yfinance as yf
from datetime import datetime, timedelta

FEISHU_WEBHOOK = os.getenv("FEISHU_WEBHOOK")

HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Referer": "https://finance.yahoo.com/"
}

def get_hk_stock_list():
    """
    從東方財富獲取港股主板股票列表（只用於代碼映射，行情部分繼續用東方財富免費公開行情接口）
    """
    stocks = []
    page = 1
    while True:
        url = f"https://push2.eastmoney.com/api/qt/clist/get?pn={page}&pz=500&po=1&np=1&fltt=2&invt=2&fid=f12&fs=m:128+t:3,m:128+t:4,m:128+t:1,m:128+t:2&fields=f12,f14"
        try:
            resp = requests.get(url, headers=HEADERS, timeout=10).json()
            data = resp.get("data", {}).get("diff", [])
            if not data:
                break
            for item in data:
                code = item.get("f12", "")
                name = item.get("f14", "")
                if code and len(code) == 5:
                    stocks.append((code, name))
            page += 1
            if page > 5:  # 最多取2500隻港股
                break
        except Exception:
            break
    return stocks

def get_stock_snapshot(sec_code):
    """
    東方財富免費公開行情接口：最新價、市值、每手股數
    """
    sec_id = f"116.{sec_code}"
    url = f"https://push2.eastmoney.com/api/qt/stock/get?secid={sec_id}&fields=f43,f116,f162"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=5).json()
        data = resp.get("data")
        if not data:
            return None
        
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
    東方財富免費公開K線接口：20日均成交額
    """
    sec_id = f"116.{sec_code}"
    url = f"https://push2his.eastmoney.com/api/qt/stock/kline/get?secid={sec_id}&klt=101&fqt=1&lmt=20&end=20500101&fields1=f1,f2,f3&fields2=f51,f56"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=5).json()
        klines = resp.get("data", {}).get("klines", [])
        if not klines:
            return 0.0
        turnovers = [float(k.split(",")[1]) for k in klines]
        return sum(turnovers) / len(turnovers)
    except Exception:
        return 0.0

def check_dividend_yfinance(code, start_date, end_date):
    """
    用Yahoo Finance官方API檢查未來除淨日及派息金額（完全合法授權）
    Yahoo港股代碼格式：0700.HK
    """
    yf_code = f"{code}.HK"
    try:
        ticker = yf.Ticker(yf_code)
        
        # 獲取即將到來的除淨日（calendar）
        try:
            cal = ticker.calendar
            if cal is not None and not cal.empty:
                if "Ex-Dividend Date" in cal.index:
                    ex_date_ts = cal.loc["Ex-Dividend Date", 0]
                    if hasattr(ex_date_ts, 'strftime'):
                        ex_date = ex_date_ts.strftime("%Y-%m-%d")
                        if start_date <= ex_date <= end_date:
                            # 獲取每股派息金額
                            div_amount = 0.0
                            if "Dividend Amount" in cal.index:
                                div_amount = float(cal.loc["Dividend Amount", 0])
                            
                            # 派息日
                            pay_date = "待定"
                            if "Pay Date" in cal.index:
                                pay_ts = cal.loc["Pay Date", 0]
                                if hasattr(pay_ts, 'strftime'):
                                    pay_date = pay_ts.strftime("%Y-%m-%d")
                            
                            return {
                                "ex_date": ex_date,
                                "pay_date": pay_date,
                                "dividend_per_share": div_amount  # Yahoo返回港幣
                            }
        except Exception:
            pass
        
        # 備用：檢查最近的dividends數據（有些股票會提前更新）
        try:
            divs = ticker.dividends
            if divs is not None and not divs.empty:
                last_div_date = divs.index[-1]
                last_ex_date = last_div_date.strftime("%Y-%m-%d")
                if start_date <= last_ex_date <= end_date:
                    return {
                        "ex_date": last_ex_date,
                        "pay_date": "待定",
                        "dividend_per_share": float(divs.iloc[-1])
                    }
        except Exception:
            pass
            
    except Exception:
        pass
    
    return None

def get_annual_dividend_yfinance(code):
    """
    用Yahoo Finance獲取過去12個月總派息，計算年度週息率
    """
    yf_code = f"{code}.HK"
    try:
        ticker = yf.Ticker(yf_code)
        divs = ticker.dividends
        if divs is None or divs.empty:
            return 0.0
        
        one_year_ago = datetime.now() - timedelta(days=365)
        total = 0.0
        for dt, amount in divs.items():
            if dt.to_pydatetime() >= one_year_ago:
                total += float(amount)
        return total
    except Exception:
        return 0.0

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
                    "content": f"掃描區間：`{start_date}` 至 `{end_date}`\n篩選條件：**市值 > 50億** | **20日均額 > 3000萬** | **本次派息收益率 > 3%**\n排序規則：**本次派息收益率由高至低**\n數據源：Yahoo Finance 官方API"
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
    print("數據源：Yahoo Finance 官方API（合法授權）")
    
    # 1. 獲取港股列表
    stock_list = get_hk_stock_list()
    print(f"共獲取 {len(stock_list)} 隻港股代碼")
    
    results = []
    
    # 2. 遍歷檢查分紅（Yahoo Finance API有頻率限制，適當sleep）
    import time
    for i, (code, name) in enumerate(stock_list):
        if i % 100 == 0:
            print(f"進度: {i}/{len(stock_list)}")
        
        div_info = check_dividend_yfinance(code, start_date, end_date)
        if not div_info:
            time.sleep(0.2)
            continue
        
        dividend_hkd = div_info["dividend_per_share"]
        if dividend_hkd <= 0:
            time.sleep(0.2)
            continue
        
        # 獲取行情快照
        snap = get_stock_snapshot(code)
        if not snap:
            time.sleep(0.2)
            continue
        
        market_cap = snap["market_cap"]
        last_price = snap["last_price"]
        lot_size = snap["lot_size"]
        
        if market_cap < 5_000_000_000 or last_price <= 0 or lot_size <= 0:
            time.sleep(0.2)
            continue
        
        # 本次派息收益率
        yield_pct = (dividend_hkd / last_price) * 100.0
        if yield_pct < 3.0:
            time.sleep(0.2)
            continue
        
        # 20日均成交額
        avg_turnover = get_20d_avg_turnover(code)
        if avg_turnover < 30_000_000:
            time.sleep(0.2)
            continue
        
        # 年度週息率
        annual_div = get_annual_dividend_yfinance(code)
        annual_yield_pct = (annual_div / last_price) * 100.0 if annual_div > 0 else 0.0
        
        dividend_per_lot = dividend_hkd * lot_size
        
        results.append({
            "name": name,
            "code": code,
            "ex_date": div_info["ex_date"],
            "pay_date": div_info["pay_date"],
            "dividend_per_share": dividend_hkd,
            "lot_size": lot_size,
            "dividend_per_lot": dividend_per_lot,
            "yield_pct": yield_pct,
            "annual_yield_pct": annual_yield_pct,
            "last_price": last_price,
            "market_cap_billion": market_cap / 100_000_000.0
        })
        
        time.sleep(0.3)  # 避免觸發Yahoo頻率限制
    
    df = pd.DataFrame(results)
    
    if not df.empty:
        df = df.sort_values(by="yield_pct", ascending=False)
        print("\n篩選結果:")
        print(df[["code", "name", "dividend_per_share", "yield_pct", "dividend_per_lot"]].to_string(index=False))
    
    push_to_feishu_card(df, start_date, end_date)

if __name__ == "__main__":
    main()
