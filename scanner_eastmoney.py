import os
import requests
import pandas as pd
from datetime import datetime, timedelta

FEISHU_WEBHOOK = os.getenv("FEISHU_WEBHOOK")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Referer": "https://data.eastmoney.com/"
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

def get_stock_snapshot(sec_code, debug=False):
    """
    東方財富push2接口：行情快照
    """
    sec_id = f"116.{sec_code}"
    # 請求更多字段，方便調試
    url = f"https://push2.eastmoney.com/api/qt/stock/get?secid={sec_id}&fields=f43,f57,f58,f116,f162,f163,f164,f167,f168,f169,f170,f171,f29,f152"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=8)
        if debug:
            print(f"  [DEBUG] {sec_code} HTTP狀態: {resp.status_code}")
            print(f"  [DEBUG] 返回內容前300字: {resp.text[:300]}")
        
        data = resp.json().get("data")
        if not data:
            return None
        
        if debug:
            print(f"  [DEBUG] 解析數據: {data}")
        
        # f43: 最新價，港股需要除以1000
        f43 = data.get("f43")
        last_price = f43 / 1000.0 if f43 and f43 != "-" else 0.0
        
        # f116: 總市值
        f116 = data.get("f116")
        market_cap = float(f116) if f116 and f116 != "-" else 0.0
        
        # f162: 每手股數
        f162 = data.get("f162")
        lot_size = int(f162) if f162 and f162 != "-" else 0
        
        # 如果f162為0，嘗試其他常見字段
        if lot_size <= 0:
            for alt_field in ["f163", "f164", "f152"]:
                val = data.get(alt_field)
                if val and val != "-" and int(val) > 0:
                    lot_size = int(val)
                    break
        
        return {
            "last_price": last_price,
            "market_cap": market_cap,
            "lot_size": lot_size
        }
    except Exception as e:
        if debug:
            print(f"  [DEBUG] 請求異常: {e}")
        return None

def get_20d_avg_turnover(sec_code):
    """
    東方財富push2his接口：20日均成交額
    """
    sec_id = f"116.{sec_code}"
    url = f"https://push2his.eastmoney.com/api/qt/stock/kline/get?secid={sec_id}&klt=101&fqt=1&lmt=20&end=20500101&fields1=f1,f2,f3&fields2=f51,f56"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=8).json()
        klines = resp.get("data", {}).get("klines", [])
        if not klines:
            return 0.0
        turnovers = [float(k.split(",")[1]) for k in klines]
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
                    "content": f"掃描區間：`{start_date}` 至 `{end_date}`\n篩選條件：**市值 > 50億** | **20日均額 > 3000萬** | **本次派息收益率 > 3%**\n排序規則：**本次派息收益率由高至低**\n數據源：東方財富公開API"
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
    
    # 1. 獲取派息日曆
    div_records = get_dividend_calendar(start_date, end_date)
    print(f"東方財富datacenter返回 {len(div_records)} 條派息記錄")
    
    if not div_records:
        print("該區間內未獲取到分紅記錄。")
        push_to_feishu_card(pd.DataFrame(), start_date, end_date)
        return
    
    results = []
    snap_success = 0
    snap_fail = 0
    turnover_success = 0
    
    # 2. 遍歷每檔股票
    for idx, item in enumerate(div_records):
        raw_code = str(item.get("SECURITY_CODE", "")).zfill(5)
        name = item.get("SECURITY_NAME_ABBR", "")
        ex_date = item.get("ZS_EX_DIVIDEND_DATE", "")[:10]
        
        dividend_hkd = item.get("DPS_HKD", 0.0)
        if not dividend_hkd or float(dividend_hkd) <= 0:
            continue
        dividend_hkd = float(dividend_hkd)
        
        # 前3隻打印詳細調試信息
        debug_mode = idx < 3
        if debug_mode:
            print(f"\n[DEBUG] 處理第 {idx+1} 隻: {name} ({raw_code})")
        
        snap = get_stock_snapshot(raw_code, debug=debug_mode)
        if not snap:
            snap_fail += 1
            if debug_mode:
                print(f"  [DEBUG] 快照獲取失敗")
            continue
        
        snap_success += 1
        
        # 測試K線接口
        avg_turnover = get_20d_avg_turnover(raw_code)
        if avg_turnover > 0:
            turnover_success += 1
        if debug_mode:
            print(f"  [DEBUG] 20日均成交額: {avg_turnover:,.0f} 港元")
            
        market_cap = snap["market_cap"]
        last_price = snap["last_price"]
        lot_size = snap["lot_size"]
        
        if debug_mode:
            print(f"  [DEBUG] 股價: {last_price:.3f}, 市值: {market_cap/1e8:.2f}億, 每手: {lot_size}股")
        
        # 條件1: 市值 > 50億
        if market_cap < 5_000_000_000:
            if debug_mode:
                print(f"  [DEBUG] 市值不足50億，跳過")
            continue
            
        if last_price <= 0 or lot_size <= 0:
            if debug_mode:
                print(f"  [DEBUG] 股價或每手股數無效，跳過")
            continue
            
        # 條件2: 本次派息收益率 > 3%
        yield_pct = (dividend_hkd / last_price) * 100.0
        if yield_pct < 3.0:
            if debug_mode:
                print(f"  [DEBUG] 收益率不足3%，跳過")
            continue
            
        # 條件3: 20日均成交額 > 3000萬
        if avg_turnover < 30_000_000:
            if debug_mode:
                print(f"  [DEBUG] 成交額不足3000萬，跳過")
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
