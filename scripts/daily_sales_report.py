#!/usr/bin/env python3
"""
Chessnut 每日销售日报
每天 00:00 生成上一日的日报
"""
import json, urllib.request, urllib.parse, os, glob, re
from datetime import datetime, timedelta, timezone
from collections import defaultdict

# === Credentials ===
CREDS_SH = json.load(open('/root/.openclaw/workspace/SHOPIFY/.shopify_token'))
CREDS_AMZ = json.load(open('/root/.openclaw/workspace/SHOPIFY/.amazon_credentials.json'))

GOOGLE_ADS = json.load(open('/root/.openclaw/workspace/SHOPIFY/.google_ads_credentials.json'))

FEISHU_APP_ID = "cli_a955861ea6391cb5"
FEISHU_APP_SECRET = "kXjJvuwsYevTkvYNLeVhXqIn7EG4dgnm"
BITABLE_TOKEN = "WT4NbDFevaoH0JshGW4c7gj8n0Q"
TABLE_ID = "tblcjH4EI4NXgYvs"

# === Helper functions ===
def get_feishu_token():
    data = json.dumps({"app_id": FEISHU_APP_ID, "app_secret": FEISHU_APP_SECRET}).encode()
    req = urllib.request.Request("https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
        data=data, headers={"Content-Type": "application/json"}, method="POST")
    return json.loads(urllib.request.urlopen(req).read())["tenant_access_token"]

def get_amz_token(region):
    rt = CREDS_AMZ["regions"][region]["refresh_token"]
    data = urllib.parse.urlencode({"grant_type": "refresh_token", "refresh_token": rt,
        "client_id": CREDS_AMZ["client_id"], "client_secret": CREDS_AMZ["client_secret"]}).encode()
    return json.loads(urllib.request.urlopen(urllib.request.Request(
        "https://api.amazon.com/auth/o2/token", data=data, method="POST")).read())["access_token"]

# === Data fetching ===
def fetch_shopify_sales(date_str):
    token = CREDS_SH['access_token']
    shop = CREDS_SH['shop']
    
    # 1. Get orders created on this date (Beijing time GMT+8)
    # date_str is in Beijing time, convert to UTC for API: -8 hours
    from datetime import datetime as dt
    bj_start = dt.strptime(f'{date_str}T00:00:00+08:00', '%Y-%m-%dT%H:%M:%S%z')
    bj_end = dt.strptime(f'{date_str}T23:59:59+08:00', '%Y-%m-%dT%H:%M:%S%z')
    utc_start = bj_start.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S+00:00')
    utc_end = bj_end.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S+00:00')
    url = f"https://{shop}/admin/api/2024-01/orders.json?status=any&created_at_min={utc_start}&created_at_max={utc_end}&limit=250&fields=id,order_number,total_price,subtotal_price,financial_status,fulfillment_status,shipping_address,line_items,refunds,cancelled_at"
    req = urllib.request.Request(url, headers={"X-Shopify-Access-Token": token})
    resp = json.loads(urllib.request.urlopen(req).read())
    orders = resp.get('orders', [])
    
    gross = 0
    net = 0
    refund_amount = 0
    refund_count = 0
    by_product = {}
    by_country = {}
    
    for o in orders:
        total = float(o.get('total_price', 0))
        gross += total
        
        # Calculate refunds from transactions
        order_refund = 0
        for r in o.get('refunds', []):
            for tx in r.get('transactions', []):
                if tx.get('kind') == 'refund' and tx.get('status') == 'success':
                    order_refund += float(tx.get('amount', 0))
        
        if order_refund > 0:
            refund_amount += order_refund
            refund_count += 1
        net += (total - order_refund)
        
        country = o.get('shipping_address', {}).get('country', 'N/A') if o.get('shipping_address') else 'Direct'
        by_country[country] = by_country.get(country, 0) + 1
        
        for item in o.get('line_items', []):
            sku = item.get('sku', 'N/A')
            qty = item.get('quantity', 0)
            price = float(item.get('price', 0)) * qty
            if sku not in by_product:
                by_product[sku] = {'qty': 0, 'revenue': 0}
            by_product[sku]['qty'] += qty
            by_product[sku]['revenue'] += price
    
    # 2. Also check for refunds PROCESSED on this date (may be from older orders)
    for status in ['refunded', 'partially_refunded']:
        r_url = f"https://{shop}/admin/api/2024-01/orders.json?financial_status={status}&limit=250&created_at_min=2026-04-01T00:00:00+00:00&fields=id,order_number,refunds,total_price"
        r_req = urllib.request.Request(r_url, headers={"X-Shopify-Access-Token": token})
        r_resp = json.loads(urllib.request.urlopen(r_req).read())
        for o in r_resp.get('orders', []):
            for r in o.get('refunds', []):
                created = r.get('created_at', '')
                # Check if refund was processed on this Beijing time date
        created_bj = ''
        if created:
            try:
                from datetime import datetime as dt2
                dt_obj = dt2.fromisoformat(created.replace('Z', '+00:00'))
                created_bj = dt_obj.astimezone(timezone(timedelta(hours=8))).strftime('%Y-%m-%d')
            except:
                created_bj = created[:10]
        if created_bj == date_str:
                    for tx in r.get('transactions', []):
                        if tx.get('kind') == 'refund' and tx.get('status') == 'success':
                            amt = float(tx.get('amount', 0))
                            # Check if this refund was already counted in the order
                            already_counted = False
                            for orig_o in orders:
                                if orig_o.get('id') == o.get('id'):
                                    already_counted = True
                                    break
                            if not already_counted:
                                refund_amount += amt
                                refund_count += 1
                                net -= amt  # Subtract from net since it's a refund processed today
    
    return {
        'orders': len(orders), 'gross': gross, 'net': net,
        'refund_amount': refund_amount, 'refund_count': refund_count,
        'by_product': by_product, 'by_country': by_country,
    }

def get_shopify_prices():
    """Get SKU -> price mapping from Shopify"""
    try:
        token = CREDS_SH['access_token']
        shop = CREDS_SH['shop']
        url = f"https://{shop}/admin/api/2024-01/products.json?limit=250&fields=id,title,variants"
        req = urllib.request.Request(url, headers={"X-Shopify-Access-Token": token})
        resp = json.loads(urllib.request.urlopen(req).read())
        prices = {}
        for p in resp.get('products', []):
            for v in p.get('variants', []):
                sku = v.get('sku', '')
                if sku:
                    prices[sku.strip().upper()] = float(v.get('price', 0))
        return prices
    except:
        return {}

def fetch_amazon_sales(date_str):
    results = {}
    shopify_prices = get_shopify_prices()
    
    # ASIN -> SKU mapping for price lookup
    ASIN_MAP = {
        "B0GF95VLBB": "CB5", "B0D4VFLKDS": "CG100-O", "B0D4ZDB4LK": "CG100-G",
        "B0CWLJ98P4": "CE100", "B0BMGBR9BW": "CA100", "B0CCVGN7RM": "CA101",
        "B0BMQFMLZ1": "PSFB1", "B0BRNC7CSJ": "CB1", "B0C3W6181R": "CP100",
        "B0C6XR4TM1": "CB2", "B0C6XRLFXY": "PSFB2", "B0C6XTNZKP": "CP1-C",
        "B0D2HSQ6RJ": "CB3", "B0FY6RRL76": "CM100-P",
    }
    
    for region, wh_code in [('na', 'FBA-US'), ('eu', 'FBA-DE')]:
        try:
            token = get_amz_token(region)
            now = datetime.now(timezone.utc)
            date_obj = datetime.strptime(date_str, '%Y-%m-%d').replace(tzinfo=timezone.utc)
            date_after = date_obj.strftime('%Y-%m-%dT00:00:00Z')
            # Convert Beijing time date to UTC for Amazon API
            bj_start = datetime.strptime(f'{date_str}T00:00:00+08:00', '%Y-%m-%dT%H:%M:%S%z')
            bj_end = datetime.strptime(f'{date_str}T23:59:59+08:00', '%Y-%m-%dT%H:%M:%S%z')
            date_after = bj_start.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
            # CreatedBefore must be at least 2 min before now
            utc_end = bj_end.astimezone(timezone.utc)
            date_before = min(utc_end, now - timedelta(minutes=2)).strftime('%Y-%m-%dT%H:%M:%SZ')
            
            marketplace = 'ATVPDKIKX0DER' if region == 'na' else 'A1PA6795UKMFR9'
            domain = 'sellingpartnerapi-na' if region == 'na' else 'sellingpartnerapi-eu'
            url = f"https://{domain}.amazon.com/orders/v0/orders?CreatedAfter={date_after}&CreatedBefore={date_before}&MarketplaceIds={marketplace}&MaxResultsPerPage=100"
            req = urllib.request.Request(url, headers={"x-amz-access-token": token})
            resp = json.loads(urllib.request.urlopen(req).read())
            orders = resp.get('payload', {}).get('Orders', [])
            
            revenue = 0
            count = 0
            shipped = 0
            pending = 0
            for o in orders:
                status = o.get('OrderStatus', '')
                if status == 'Canceled':
                    continue
                ot = o.get('OrderTotal', {})
                amount = float(ot.get('Amount', 0)) if ot else 0
                oid = o.get('AmazonOrderId', '')
                
                if status == 'Shipped' and amount > 0:
                    shipped += 1
                    count += 1
                    revenue += amount
                elif amount > 0:
                    # Pending/Unshipped with known amount
                    count += 1
                    pending += 1
                    revenue += amount
                else:
                    # Pending with no amount - estimate from Shopify prices
                    pending += 1
                    count += 1
                    try:
                        items_url = f"{domain}.amazon.com/orders/v0/orders/{oid}/orderItems"
                        items_req = urllib.request.Request(f"https://{items_url}", headers={"x-amz-access-token": token})
                        items_resp = json.loads(urllib.request.urlopen(items_req).read())
                        for item in items_resp.get('payload', {}).get('OrderItems', []):
                            asin = item.get('ASIN', '')
                            sku = ASIN_MAP.get(asin, item.get('SellerSKU', ''))
                            sku_upper = sku.upper() if sku else ''
                            qty = int(item.get('QuantityOrdered', 0))
                            price = shopify_prices.get(sku_upper, 0)
                            if price > 0:
                                revenue += price * qty
                    except:
                        pass
            
            results[wh_code] = {'revenue': revenue, 'orders': count, 'shipped': shipped, 'pending': pending}
        except Exception as e:
            results[wh_code] = {'revenue': 0, 'orders': 0, 'shipped': 0, 'pending': 0, 'error': str(e)}
    
    return results

def fetch_google_ads(date_str):
    try:
        from google.ads.googleads.client import GoogleAdsClient
        client = GoogleAdsClient.load_from_dict({
            "developer_token": GOOGLE_ADS["developer_token"],
            "client_id": GOOGLE_ADS["client_id"],
            "client_secret": GOOGLE_ADS["client_secret"],
            "refresh_token": GOOGLE_ADS["refresh_token"],
            "use_proto_plus": True,
        })
        
        ga_service = client.get_service("GoogleAdsService")
        # Google Ads uses the account's timezone, so we pass the Beijing date directly
        query = f"""
        SELECT metrics.impressions, metrics.clicks, metrics.cost_micros,
               metrics.conversions, metrics.conversions_value
        FROM campaign 
        WHERE segments.date = '{date_str}'
          AND campaign.status = 'ENABLED'
        """
        
        response = ga_service.search_stream(customer_id=GOOGLE_ADS["customer_id"], query=query)
        
        total_cost = 0
        total_clicks = 0
        total_impressions = 0
        total_conversions = 0
        total_conv_value = 0
        
        for batch in response:
            for row in batch.results:
                total_impressions += row.metrics.impressions
                total_clicks += row.metrics.clicks
                total_cost += row.metrics.cost_micros / 1_000_000
                total_conversions += row.metrics.conversions
                total_conv_value += row.metrics.conversions_value
        
        cpc = total_cost / total_clicks if total_clicks > 0 else 0
        roas = total_conv_value / total_cost if total_cost > 0 else 0
        ctr = total_clicks / total_impressions * 100 if total_impressions > 0 else 0
        
        return {
            'cost': total_cost, 'clicks': total_clicks, 'impressions': total_impressions,
            'conversions': total_conversions, 'conv_value': total_conv_value,
            'cpc': cpc, 'roas': roas, 'ctr': ctr,
        }
    except Exception as e:
        return {'cost': 0, 'error': str(e)}

def fetch_upcoming_shipments():
    try:
        token = get_feishu_token()
        now = datetime.now(timezone.utc)
        soon = now + timedelta(days=14)
        
        all_records = []
        page_token = None
        while True:
            url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{BITABLE_TOKEN}/tables/{TABLE_ID}/records?page_size=100"
            if page_token: url += f"&page_token={page_token}"
            req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
            resp = json.loads(urllib.request.urlopen(req).read())
            all_records.extend(resp['data']['items'])
            if not resp['data'].get('has_more'): break
            page_token = resp['data'].get('page_token')
        
        upcoming = []
        for r in all_records:
            f = r.get('fields', {})
            if f.get('状态') not in ('在途', '到港'):
                continue
            eta_port = f.get('预计到港日')
            if not eta_port:
                continue
            eta_dt = datetime.fromtimestamp(eta_port/1000, tz=timezone.utc)
            days_away = (eta_dt - now).days
            if 0 <= days_away <= 14:
                upcoming.append({
                    'batch': f.get('批次号', ''), 'sku': f.get('SKU', ''),
                    'dest': f.get('目的仓', ''), 'qty': int(f.get('数量', 0)) if f.get('数量') else 0,
                    'eta': eta_dt.strftime('%m/%d'), 'days': days_away,
                    'note': f.get('备注', ''),
                })
        
        upcoming.sort(key=lambda x: x['days'])
        return upcoming
    except:
        return []

def fetch_transit_exceptions():
    try:
        token = get_feishu_token()
        all_records = []
        page_token = None
        while True:
            url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{BITABLE_TOKEN}/tables/{TABLE_ID}/records?page_size=100"
            if page_token: url += f"&page_token={page_token}"
            req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
            resp = json.loads(urllib.request.urlopen(req).read())
            all_records.extend(resp['data']['items'])
            if not resp['data'].get('has_more'): break
            page_token = resp['data'].get('page_token')
        
        exceptions = []
        for r in all_records:
            f = r.get('fields', {})
            note = f.get('备注', '')
            if not note:
                continue
            status = f.get('状态', '')
            if status in ('在途', '到港', '退运中'):
                exceptions.append({
                    'batch': f.get('批次号', ''), 'sku': f.get('SKU', ''),
                    'dest': f.get('目的仓', ''), 'qty': int(f.get('数量', 0)) if f.get('数量') else 0,
                    'status': status, 'note': note,
                })
        return exceptions
    except:
        return []

def fetch_inventory_alerts():
    try:
        with open('/root/.openclaw/workspace/SHOPIFY/data/inventory_report.json') as f:
            inv = json.load(f)
        
        critical = []
        for wh, products in inv.get('warehouses', {}).items():
            if wh in ('summary', 'transit', 'forecast'):
                continue
            for p in products:
                d = p.get('days_of_stock', 9999)
                if 0 < d <= 14:
                    wh_name = {'FBA-US':'Amazon US','FBA-DE':'Amazon EU','USWE':'谷仓美西','CZ':'谷仓捷克','HK':'香港仓','AU':'谷仓澳洲','CA':'谷仓加拿大'}.get(wh, wh)
                    critical.append({'wh': wh_name, 'sku': p['sku'], 'stock': p['stock'], 'transit': p.get('in_transit', 0), 'days': d})
        
        critical.sort(key=lambda x: x['days'])
        return critical
    except:
        return []

def fetch_chess_news():
    try:
        with open('/root/.openclaw/workspace/memory/chess-news-monitor.md') as f:
            content = f.read()
        
        # Extract today's highlights (lines with 🔴)
        today = datetime.now().strftime('%Y.%m.%d')
        highlights = []
        in_today = False
        for line in content.split('\n'):
            if line.startswith(f'### {today}'):
                in_today = True
                continue
            if line.startswith('### ') and in_today:
                break
            if in_today and '🔴' in line:
                clean = line.strip().lstrip('- 🔴 ').strip()
                if clean:
                    highlights.append(clean)
        
        return highlights[:5]
    except:
        return []

def fetch_chessnut_brand_monitor():
    """Returns a dict with full sentiment monitoring data for the daily report"""
    import glob as glob_mod
    
    xiu_files = sorted(glob_mod.glob('/root/.openclaw/workspace-xiu/memory/*chessnut*monitor*.md'), reverse=True)
    
    for fpath in xiu_files[:2]:
        try:
            with open(fpath) as f:
                file_content = f.read()
            
            result = {
                'date': '', 'status': '', 'stats': {},
                'notable': [], 'positive': [], 'neutral': [],
                'recommendations': [], 'reddit_summary': '',
                'competitors': [],
            }
            
            basename = os.path.basename(fpath)
            date_match = re.search(r'(\d{4}-\d{2}-\d{2})', basename)
            result['date'] = date_match.group(1) if date_match else ''
            
            lines = file_content.split('\n')
            
            # Parse summary stats
            for line in lines:
                if 'CRITICAL:' in line:
                    result['stats']['critical'] = line.split(':')[-1].strip()
                elif 'NOTABLE:' in line:
                    result['stats']['notable'] = line.split(':')[-1].strip()
                elif 'POSITIVE:' in line:
                    result['stats']['positive'] = line.split(':')[-1].strip()
                elif 'NEUTRAL:' in line:
                    result['stats']['neutral'] = line.split(':')[-1].strip()
                elif '整体健康状态' in line:
                    result['status'] = line.split(':')[-1].strip().replace('**', '').strip()
            
            # Parse notable mentions with full details
            in_section = False
            for i, line in enumerate(lines):
                if '## 🟡 Notable' in line:
                    in_section = True
                    continue
                if in_section and line.startswith('## '):
                    break
                if in_section and '**' in line and line.strip().startswith(('1.', '2.', '3.')):
                    parts = line.split('**', 2)
                    if len(parts) >= 3:
                        title = parts[1].strip()
                        summary = ''
                        concern = ''
                        for j in range(i+1, min(i+10, len(lines))):
                            if '摘要:' in lines[j]:
                                summary = lines[j].split('摘要:')[-1].strip()[:200]
                            if '关注点:' in lines[j]:
                                concern = lines[j].split('关注点:')[-1].strip()[:150]
                            if lines[j].strip().startswith(('**', '##')) and j > i+1:
                                break
                        result['notable'].append({'title': title, 'summary': summary, 'concern': concern})
            
            # Parse positive mentions with full details
            in_section = False
            for i, line in enumerate(lines):
                if '## ✅ Positive' in line:
                    in_section = True
                    continue
                if in_section and line.startswith('## '):
                    break
                if in_section and '**' in line and line.strip().startswith(('1.', '2.', '3.', '4.', '5.')):
                    parts = line.split('**', 2)
                    if len(parts) >= 3:
                        title = parts[1].strip()
                        summary = ''
                        for j in range(i+1, min(i+8, len(lines))):
                            if '摘要:' in lines[j]:
                                summary = lines[j].split('摘要:')[-1].strip()[:150]
                                break
                            if lines[j].strip().startswith(('**', '##', '1.', '2.')) and j > i+1:
                                break
                        result['positive'].append({'title': title, 'summary': summary})
            
            # Parse neutral mentions
            in_section = False
            for i, line in enumerate(lines):
                if '## 🟢 Neutral' in line:
                    in_section = True
                    continue
                if in_section and line.startswith('## '):
                    break
                if in_section and '**' in line and line.strip().startswith(('1.', '2.', '3.')):
                    parts = line.split('**', 2)
                    if len(parts) >= 3:
                        title = parts[1].strip()
                        summary = ''
                        for j in range(i+1, min(i+8, len(lines))):
                            if '摘要:' in lines[j]:
                                summary = lines[j].split('摘要:')[-1].strip()[:120]
                                break
                            if lines[j].strip().startswith(('**', '##', '1.', '2.')) and j > i+1:
                                break
                        result['neutral'].append({'title': title, 'summary': summary})
            
            # Parse Reddit summary
            in_section = False
            for line in lines:
                if '## Reddit 专项' in line:
                    in_section = True
                    continue
                if in_section and line.startswith('## '):
                    break
                if in_section and '摘要' in line:
                    result['reddit_summary'] = line.split('摘要')[-1].lstrip('**:').strip()[:250]
            
            # Parse competitor updates - ONLY pure competitor news (not Chessnut comparisons)
            competitor_keywords = ['SenseRobot', 'Square Off', 'Phantom', 'ChessUp.*(?:launch|kickstarter|new|price|partner|update)', 'DGT.*(?:new|launch|partner)']
            chessnut_keywords = ['Chessnut', 'chessnut', 'chessnutech', 'Chessnut Evo', 'Chessnut Go', 'Chessnut Move']
            
            # Check for dedicated 竞品 section
            in_section = False
            for i, line in enumerate(lines):
                if '竞品' in line and line.startswith('##'):
                    in_section = True
                    continue
                if in_section and line.startswith('## '):
                    break
                if in_section and line.strip().startswith(('- ', '* ')):
                    text = line.strip().lstrip('- *').replace('**', '').strip()
                    # Skip if it's mainly about Chessnut
                    if text and len(text) > 15:
                        chessnut_count = sum(1 for kw in chessnut_keywords if kw in text)
                        if chessnut_count == 0 or (chessnut_count == 1 and 'Chessnut' in text and len(text) > 60):
                            result['competitors'].append(text[:200])
            
            # Parse Assessment recommendations
            in_section = False
            for line in lines:
                if '## Assessment' in line:
                    in_section = True
                    continue
                if in_section and line.startswith('## '):
                    break
                if in_section and line.startswith('- ⚠️'):
                    result['recommendations'].append(line.lstrip('- ⚠️ ').strip()[:100])
                elif in_section and line.startswith('- 📌'):
                    result['recommendations'].append(line.lstrip('- 📌 ').strip()[:100])
            
            if result['status']:
                return result
        except:
            continue
    
    return None

# === Report generation ===
def generate_report(date_str):
    # Parse date for display
    date_obj = datetime.strptime(date_str, '%Y-%m-%d')
    weekdays = ['周一', '周二', '周三', '周四', '周五', '周六', '周日']
    weekday = weekdays[date_obj.weekday()]
    display_date = f"{date_obj.year}年{date_obj.month}月{date_obj.day}日"
    
    print(f"📊 Chessnut 每日销售日报 — {display_date}（{weekday}）")
    print(f"{'='*50}")
    
    # 1. Shopify sales
    shopify = fetch_shopify_sales(date_str)
    print(f"\n💰 销售概览")
    print(f"   Shopify 毛销售:  ${shopify['gross']:,.0f} ({shopify['orders']}单)")
    if shopify['refund_amount'] > 0:
        print(f"   Shopify 退款:    -${shopify['refund_amount']:,.0f} ({shopify['refund_count']}笔)")
    print(f"   Shopify 净销售:  ${shopify['net']:,.0f}")
    
    # 2. Amazon sales
    amazon = fetch_amazon_sales(date_str)
    amz_total = sum(v['revenue'] for v in amazon.values())
    amz_orders = sum(v['orders'] for v in amazon.values())
    amz_shipped = sum(v.get('shipped', 0) for v in amazon.values())
    amz_pending = sum(v.get('pending', 0) for v in amazon.values())
    
    for wh, data in amazon.items():
        if data['orders'] > 0:
            currency = '$' if wh == 'FBA-US' else '€'
            rev_str = f"{currency}{data['revenue']:,.0f}" if data['revenue'] > 0 else f"待确认({data['pending']}单待发货)"
            print(f"   Amazon {wh.replace('FBA-','')}: {rev_str} ({data['orders']}单)")
    
    total_net = shopify['net'] + amz_total
    total_orders = shopify['orders'] + amz_orders
    print(f"   ───────────────────────")
    print(f"   全渠道净销售:    ${total_net:,.0f} ({total_orders}单)")
    
    # 3. Top products
    if shopify['by_product']:
        print(f"\n🏆 Shopify 热销 Top 5")
        top5 = sorted(shopify['by_product'].items(), key=lambda x: -x[1]['revenue'])[:5]
        for i, (sku, d) in enumerate(top5, 1):
            pct = d['revenue'] / shopify['gross'] * 100 if shopify['gross'] > 0 else 0
            print(f"   {i}. {sku}: {d['qty']}件, ${d['revenue']:,.0f} ({pct:.0f}%)")
    
    # 4. Geographic distribution
    if shopify['by_country']:
        print(f"\n🌍 订单地区")
        top_countries = sorted(shopify['by_country'].items(), key=lambda x: -x[1])[:5]
        print(f"   " + ", ".join(f"{c}({n})" for c, n in top_countries))
    
    # 5. Inventory alerts
    alerts = fetch_inventory_alerts()
    if alerts:
        print(f"\n🔴 库存紧急（≤14天）")
        for a in alerts[:8]:
            print(f"   [{a['wh']}] {a['sku']}: 库存{a['stock']}+在途{a['transit']}, 仅剩{a['days']}天")
        if len(alerts) > 8:
            print(f"   ... 还有 {len(alerts)-8} 个SKU")
    
    # 6. Upcoming shipments
    upcoming = fetch_upcoming_shipments()
    if upcoming:
        print(f"\n🚢 近期到港（14天内）")
        # Group by ETA date
        by_date = defaultdict(list)
        for u in upcoming:
            by_date[u['eta']].append(u)
        for eta in sorted(by_date.keys()):
            items = by_date[eta]
            total_qty = sum(u['qty'] for u in items)
            days = items[0]['days']
            skus = ', '.join(f"{u['sku']}({u['qty']})" for u in items[:4])
            extra = f" +{len(items)-4}批" if len(items) > 4 else ""
            print(f"   {eta}({days}天后): {skus}{extra} → {items[0]['dest']} 共{total_qty}件")
    
    # 7. Transit exceptions
    exceptions = fetch_transit_exceptions()
    if exceptions:
        print(f"\n⚠️ 海运异常")
        for e in exceptions:
            print(f"   • {e['sku']} → {e['dest']} ({e['qty']}件): {e['note'][:60]}")
    
    # 8. Google Ads
    ads = fetch_google_ads(date_str)
    if ads.get('cost', 0) > 0:
        print(f"\n📊 广告数据")
        print(f"   花费: ${ads['cost']:,.0f} | 转化价值: ${ads['conv_value']:,.0f} | ROAS: {ads['roas']:.2f}x")
        
        # Calculate ROI based on Shopify net sales
        if shopify['net'] > 0 and ads['cost'] > 0:
            roi = (shopify['net'] - ads['cost']) / ads['cost'] * 100
            print(f"   ROI: {roi:.0f}% (基于Shopify净销售)")
    elif ads.get('error'):
        print(f"\n📊 广告数据: 获取失败")
    
    # 9. Chess news highlights
    news = fetch_chess_news()
    if news:
        print(f"\n♟️ 行业动态")
        for n in news[:3]:
            print(f"   • {n[:80]}")
    
    # 9b. Chessnut brand monitoring
    brand = fetch_chessnut_brand_monitor()
    if brand:
        print(f"\n🔍 Chessnut 舆情监控")
        stats = brand.get('stats', {})
        print(f"   扫描日期: {brand['date']} | 状态: {brand['status']}")
        print(f"   🔴严重{stats.get('critical',0)} 🟡关注{stats.get('notable',0)} ✅正面{stats.get('positive',0)} 🟢中性{stats.get('neutral',0)}")
        
        if brand.get('notable'):
            print(f"   ⚠️ 需关注:")
            for n in brand['notable'][:3]:
                print(f"      • {n['title'][:60]}")
                if n.get('summary'):
                    print(f"        {n['summary'][:70]}")
        
        if brand.get('positive'):
            print(f"   ✅ 正面:")
            for p in brand['positive'][:3]:
                print(f"      • {p[:60]}")
        
        if brand.get('recommendations'):
            print(f"   📌 建议:")
            for r in brand['recommendations'][:2]:
                print(f"      • {r[:70]}")
    
    # 10. Key metrics
    print(f"\n📈 关键指标")
    if shopify['gross'] > 0:
        print(f"   退款率: {shopify['refund_amount']/shopify['gross']*100:.1f}%")
    if ads.get('cost', 0) > 0 and total_net > 0:
        ad_pct = ads['cost'] / total_net * 100
        print(f"   广告费率: {ad_pct:.1f}% (广告花费/净销售)")
    
    print(f"\n{'='*50}")
    print(f"数据更新: {datetime.now().strftime('%Y-%m-%d %H:%M')}")

if __name__ == '__main__':
    import sys
    if len(sys.argv) > 1:
        date_str = sys.argv[1]
    else:
        # Default to yesterday (Beijing time)
        bj_now = datetime.now(timezone(timedelta(hours=8)))
        yesterday = bj_now - timedelta(days=1)
        date_str = yesterday.strftime('%Y-%m-%d')
    
    generate_report(date_str)
