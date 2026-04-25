#!/usr/bin/env python3
"""
Chessnut 综合报告系统
- 每日生成报告保存为独立文件
- 主页面默认显示最新日报
- 日报/周报/月报切换标签
- 日期选择控件
"""
import json, urllib.request, urllib.parse, os, shutil, sys
from datetime import datetime, timedelta, timezone
from collections import defaultdict

sys.path.insert(0, '/root/.openclaw/workspace/SHOPIFY/scripts')
import daily_sales_report as dsr
import importlib
importlib.reload(dsr)

BASE_DIR = '/var/www/dashboard/reports'
MAIN_HTML = '/var/www/dashboard/sales-report.html'

def ensure_dirs():
    for d in ['daily', 'weekly', 'monthly']:
        os.makedirs(f'{BASE_DIR}/{d}', exist_ok=True)

def get_available_reports(report_type):
    """Get list of available report dates"""
    report_dir = f'{BASE_DIR}/{report_type}'
    if not os.path.exists(report_dir):
        return []
    return sorted([f.replace('.html','') for f in os.listdir(report_dir) if f.endswith('.html')], reverse=True)

def generate_daily_report(report_date=None):
    """Generate and save a daily report for the given date"""
    ensure_dirs()
    
    if not report_date:
        bj_now = datetime.now(timezone(timedelta(hours=8)))
        report_date = (bj_now - timedelta(days=1)).strftime('%Y-%m-%d')
    
    output = f'{BASE_DIR}/daily/{report_date}.html'
    
    if os.path.exists(output):
        print(f"⏭️ 已存在: {output}")
        return output
    
    shopify = dsr.fetch_shopify_sales(report_date)
    amazon = dsr.fetch_amazon_sales(report_date)
    ads = dsr.fetch_google_ads(report_date)
    alerts = dsr.fetch_inventory_alerts()
    upcoming = dsr.fetch_upcoming_shipments()
    exceptions = dsr.fetch_transit_exceptions()
    brand = dsr.fetch_chessnut_brand_monitor()
    
    content = build_report_content(shopify, amazon, ads, alerts, upcoming, exceptions, brand)
    
    # Save only the content (no HTML wrapper)
    with open(output, 'w', encoding='utf-8') as f:
        f.write(content)
    print(f"✅ 日报: {output}")
    return output

def build_report_content(shopify, amazon, ads, alerts, upcoming, exceptions, brand):
    """Build just the report content HTML (no wrapper)"""
    content = ''
    
    # Sales overview
    total_net = shopify['net']
    amz_rev = sum(v['revenue'] for v in amazon.values())
    amz_orders = sum(v['orders'] for v in amazon.values())
    amz_pending = sum(v.get('pending', 0) for v in amazon.values())
    total_net += amz_rev
    total_orders = shopify['orders'] + amz_orders
    
    content += '<div class="cards">'
    content += f'<div class="card total"><div class="card-num">${total_net:,.0f}</div><div class="card-label">全渠道净销售 ({total_orders}单)</div></div>'
    content += f'<div class="card blue"><div class="card-num">${shopify["net"]:,.0f}</div><div class="card-label">Shopify 净销售 ({shopify["orders"]}单)</div></div>'
    if shopify['refund_amount'] > 0:
        content += f'<div class="card red"><div class="card-num">-${shopify["refund_amount"]:,.0f}</div><div class="card-label">Shopify 退款 ({shopify["refund_count"]}笔)</div></div>'
    if amz_rev > 0:
        has_est = amz_pending > 0
        label = f'Amazon 预估 ({amz_orders}单)' if has_est else f'Amazon 总销售 ({amz_orders}单)'
        content += f'<div class="card orange"><div class="card-num">${amz_rev:,.0f}</div><div class="card-label">{label}</div></div>'
    elif amz_orders > 0:
        content += f'<div class="card"><div class="card-num" style="color:var(--text2)">{amz_pending}待发货</div><div class="card-label">Amazon ({amz_orders}单)</div></div>'
    for wh, data in amazon.items():
        if data['orders'] > 0:
            site = wh.replace('FBA-', '')
            if data['revenue'] > 0:
                content += f'<div class="card"><div class="card-num">${data["revenue"]:,.0f}</div><div class="card-label">Amazon {site} ({data["orders"]}单)</div></div>'
            else:
                content += f'<div class="card"><div class="card-num" style="color:var(--text2)">{data["pending"]}待发</div><div class="card-label">Amazon {site} ({data["orders"]}单)</div></div>'
    content += '</div>'
    
    # Google Ads
    if ads.get('cost', 0) > 0:
        content += '<h3>📊 广告数据</h3><div class="cards">'
        content += f'<div class="card"><div class="card-num">${ads["cost"]:,.0f}</div><div class="card-label">广告花费</div></div>'
        content += f'<div class="card"><div class="card-num">${ads["conv_value"]:,.0f}</div><div class="card-label">转化价值</div></div>'
        content += f'<div class="card"><div class="card-num">{ads["roas"]:.2f}x</div><div class="card-label">ROAS</div></div>'
        if shopify['net'] > 0:
            roi = (shopify['net'] - ads['cost']) / ads['cost'] * 100
            content += f'<div class="card"><div class="card-num">{roi:.0f}%</div><div class="card-label">ROI</div></div>'
        content += '</div>'
    
    # Key metrics
    content += '<h3>📈 关键指标</h3><div class="cards">'
    if shopify['gross'] > 0:
        refund_rate = shopify['refund_amount'] / shopify['gross'] * 100
        content += f'<div class="card"><div class="card-num">{refund_rate:.1f}%</div><div class="card-label">退款率</div></div>'
    if ads.get('cost', 0) > 0 and total_net > 0:
        ad_pct = ads['cost'] / total_net * 100
        content += f'<div class="card"><div class="card-num">{ad_pct:.1f}%</div><div class="card-label">广告费率</div></div>'
    content += '</div>'
    
    # Top products
    if shopify['by_product']:
        content += '<h3>🏆 Shopify 热销 Top 10</h3>'
        content += '<table><tr><th>#</th><th>SKU</th><th>数量</th><th>金额</th><th>占比</th></tr>'
        top10 = sorted(shopify['by_product'].items(), key=lambda x: -x[1]['revenue'])[:10]
        for i, (sku, d) in enumerate(top10, 1):
            pct = d['revenue'] / shopify['gross'] * 100 if shopify['gross'] > 0 else 0
            content += f'<tr><td>{i}</td><td><strong>{sku}</strong></td><td>{d["qty"]}件</td><td>${d["revenue"]:,.0f}</td><td>{pct:.0f}%</td></tr>'
        content += '</table>'
    
    # Geographic distribution
    if shopify['by_country']:
        content += '<h3>🌍 订单地区</h3><div class="tags">'
        for c, n in sorted(shopify['by_country'].items(), key=lambda x: -x[1]):
            content += f'<span class="tag">{c} ({n})</span>'
        content += '</div>'
    
    # Inventory alerts
    if alerts:
        transit_eta = {}
        for u in upcoming:
            sku = u['sku']
            dest = u['dest']
            if sku not in transit_eta:
                transit_eta[sku] = {}
            wh_map = {'美西仓':'谷仓美西','捷克仓':'谷仓捷克','澳洲仓':'谷仓澳洲','加拿大仓':'谷仓加拿大','Amazon FBA - 美国':'Amazon US','Amazon FBA - 德国':'Amazon EU'}
            wh_name = wh_map.get(dest, dest)
            if wh_name not in transit_eta[sku] or u['eta'] < transit_eta[sku][wh_name]:
                transit_eta[sku][wh_name] = u['eta']
        
        content += '<h3 class="red">🔴 库存紧急（≤14天）</h3>'
        content += '<table><tr><th>仓库</th><th>SKU</th><th>库存</th><th>在途</th><th>预计上架</th><th>可撑天数</th></tr>'
        for a in alerts[:10]:
            bar_cls = 'bar-danger' if a['days'] <= 7 else 'bar-warning'
            eta_str = ''
            if a['sku'] in transit_eta and a['wh'] in transit_eta[a['sku']]:
                eta_str = transit_eta[a['sku']][a['wh']]
            elif a['sku'] in transit_eta:
                for wh, eta in transit_eta[a['sku']].items():
                    if a['wh'] in wh or wh in a['wh']:
                        eta_str = eta
                        break
            transit_display = f'{a["transit"]}件' if a['transit'] > 0 else '-'
            eta_display = f'→{eta_str}' if eta_str and a['transit'] > 0 else ''
            content += f'<tr><td>{a["wh"]}</td><td><strong>{a["sku"]}</strong></td><td>{a["stock"]}</td><td>{transit_display} {eta_display}</td><td>{eta_str if eta_str else "-"}</td><td><div class="days-cell"><div class="bar-container"><div class="bar {bar_cls}" style="width:{min(100,a["days"]/14*100)}%"></div></div><span>{a["days"]}天</span></div></td></tr>'
        content += '</table>'
    
    # Upcoming shipments
    if upcoming:
        content += '<h3>🚢 近期到港（14天内）</h3>'
        by_date = defaultdict(list)
        for u in upcoming:
            by_date[u['eta']].append(u)
        content += '<table><tr><th>到港日</th><th>天数</th><th>SKU</th><th>目的地</th><th>数量</th></tr>'
        for eta in sorted(by_date.keys()):
            items = by_date[eta]
            total_qty = sum(u['qty'] for u in items)
            days = items[0]['days']
            skus = ', '.join(f"{u['sku']}({u['qty']})" for u in items[:4])
            extra = f" +{len(items)-4}批" if len(items) > 4 else ""
            content += f'<tr><td>{eta}</td><td>{days}天后</td><td>{skus}{extra}</td><td>{items[0]["dest"]}</td><td>{total_qty}件</td></tr>'
        content += '</table>'
    
    # Transit exceptions
    if exceptions:
        content += '<h3 class="red">⚠️ 海运异常</h3>'
        for e in exceptions:
            border_color = '#f87171' if e['status'] == '退运中' else '#fbbf24'
            content += f'<div class="alert-card" style="border-left-color:{border_color}">'
            content += f'<div class="alert-title">{e["sku"]} → {e["dest"]} ({e["qty"]}件) <span class="status" style="color:{border_color}">[{e["status"]}]</span></div>'
            content += f'<div class="alert-desc">{e["note"][:120]}</div>'
            content += '</div>'
    
    # Chessnut brand monitoring
    if brand:
        content += '<h3>🔍 Chessnut 舆情监控</h3>'
        stats = brand.get('stats', {})
        content += f'<div class="meta">扫描: {brand["date"]} | 状态: {brand["status"]} | 🔴严重{stats.get("critical",0)} 🟡关注{stats.get("notable",0)} ✅正面{stats.get("positive",0)} 🟢中性{stats.get("neutral",0)}</div>'
        
        if brand.get('notable'):
            content += '<h4 style="color:#fbbf24;margin:12px 0 6px;font-size:14px">⚠️ 需关注</h4>'
            for n in brand['notable']:
                content += f'<div class="alert-card" style="border-left-color:#fbbf24">'
                content += f'<div class="alert-title">{n["title"]}</div>'
                if n.get('summary'):
                    content += f'<div class="alert-desc">{n["summary"]}</div>'
                if n.get('concern'):
                    content += f'<div class="alert-desc" style="color:#fbbf24;margin-top:4px">关注点: {n["concern"]}</div>'
                content += '</div>'
        
        if brand.get('positive'):
            content += '<h4 style="color:#22c55e;margin:12px 0 6px;font-size:14px">✅ 正面</h4>'
            for p in brand['positive']:
                content += f'<div class="alert-card" style="border-left-color:#22c55e">'
                content += f'<div class="alert-title">{p["title"]}</div>'
                if p.get('summary'):
                    content += f'<div class="alert-desc">{p["summary"]}</div>'
                content += '</div>'
        
        if brand.get('neutral'):
            content += '<h4 style="color:var(--text2);margin:12px 0 6px;font-size:14px">🟢 中性</h4>'
            for n in brand['neutral']:
                content += f'<div class="alert-card" style="border-left-color:var(--text2)">'
                content += f'<div class="alert-title">{n["title"]}</div>'
                if n.get('summary'):
                    content += f'<div class="alert-desc">{n["summary"]}</div>'
                content += '</div>'
        
        if brand.get('reddit_summary'):
            content += f'<div class="meta" style="margin-top:10px">📱 Reddit: {brand["reddit_summary"]}</div>'
        
        if brand.get('recommendations'):
            content += '<h4 style="color:var(--accent);margin:12px 0 6px;font-size:14px">📌 建议</h4>'
            for r in brand['recommendations']:
                content += f'<div class="meta">• {r}</div>'
    
    return content

def generate_weekly_report(week_str):
    """Generate a weekly report by aggregating daily reports for the given week (YYYY-WXX)"""
    ensure_dirs()
    output = f'{BASE_DIR}/weekly/{week_str}.html'
    if os.path.exists(output):
        print(f"⏭️ 周报已存在: {output}")
        return output
    
    # Find all daily reports in this week
    year, week_num = week_str.split('-W')
    # Calculate week start/end dates
    jan1 = datetime(int(year), 1, 1)
    week_start = jan1 + timedelta(weeks=int(week_num)-1)
    # Adjust to Monday
    week_start = week_start - timedelta(days=week_start.weekday())
    week_end = week_start + timedelta(days=6)
    
    # Aggregate data from daily reports
    total_shopify_gross = 0
    total_shopify_net = 0
    total_shopify_refund = 0
    total_shopify_orders = 0
    total_amz_rev = 0
    total_amz_orders = 0
    total_ads_cost = 0
    total_ads_conv = 0
    all_products = defaultdict(lambda: {'qty': 0, 'revenue': 0})
    all_countries = defaultdict(int)
    
    for day_offset in range(7):
        day = week_start + timedelta(days=day_offset)
        day_str = day.strftime('%Y-%m-%d')
        try:
            shopify = dsr.fetch_shopify_sales(day_str)
            amazon = dsr.fetch_amazon_sales(day_str)
            ads = dsr.fetch_google_ads(day_str)
            
            total_shopify_gross += shopify['gross']
            total_shopify_net += shopify['net']
            total_shopify_refund += shopify['refund_amount']
            total_shopify_orders += shopify['orders']
            
            for wh, data in amazon.items():
                total_amz_rev += data.get('revenue', 0)
                total_amz_orders += data.get('orders', 0)
            
            total_ads_cost += ads.get('cost', 0)
            total_ads_conv += ads.get('conv_value', 0)
            
            for sku, d in shopify.get('by_product', {}).items():
                all_products[sku]['qty'] += d['qty']
                all_products[sku]['revenue'] += d['revenue']
            for c, n in shopify.get('by_country', {}).items():
                all_countries[c] += n
        except:
            pass
    
    # Build content
    content = f'<div class="meta">📅 {week_start.strftime("%m/%d")} - {week_end.strftime("%m/%d")} (第{week_num}周)</div>\n'
    
    total_net = total_shopify_net + total_amz_rev
    total_orders = total_shopify_orders + total_amz_orders
    
    content += '<div class="cards">'
    content += f'<div class="card total"><div class="card-num">${total_net:,.0f}</div><div class="card-label">全渠道净销售 ({total_orders}单)</div></div>'
    content += f'<div class="card blue"><div class="card-num">${total_shopify_net:,.0f}</div><div class="card-label">Shopify 净销售 ({total_shopify_orders}单)</div></div>'
    if total_shopify_refund > 0:
        content += f'<div class="card red"><div class="card-num">-${total_shopify_refund:,.0f}</div><div class="card-label">退款</div></div>'
    if total_amz_rev > 0:
        content += f'<div class="card orange"><div class="card-num">${total_amz_rev:,.0f}</div><div class="card-label">Amazon ({total_amz_orders}单)</div></div>'
    content += '</div>'
    
    # Top 10 products
    if all_products:
        content += '<h3>🏆 Shopify 热销 Top 10</h3>'
        content += '<table><tr><th>#</th><th>SKU</th><th>数量</th><th>金额</th><th>占比</th></tr>'
        top10 = sorted(all_products.items(), key=lambda x: -x[1]['revenue'])[:10]
        for i, (sku, d) in enumerate(top10, 1):
            pct = d['revenue'] / total_shopify_gross * 100 if total_shopify_gross > 0 else 0
            content += f'<tr><td>{i}</td><td><strong>{sku}</strong></td><td>{d["qty"]}件</td><td>${d["revenue"]:,.0f}</td><td>{pct:.0f}%</td></tr>'
        content += '</table>'
    
    # Countries
    if all_countries:
        content += '<h3>🌍 订单地区</h3><div class="tags">'
        for c, n in sorted(all_countries.items(), key=lambda x: -x[1]):
            content += f'<span class="tag">{c} ({n})</span>'
        content += '</div>'
    
    # Ads
    if total_ads_cost > 0:
        content += '<h3>📊 广告数据</h3><div class="cards">'
        content += f'<div class="card"><div class="card-num">${total_ads_cost:,.0f}</div><div class="card-label">广告花费</div></div>'
        content += f'<div class="card"><div class="card-num">${total_ads_conv:,.0f}</div><div class="card-label">转化价值</div></div>'
        roas = total_ads_conv / total_ads_cost if total_ads_cost > 0 else 0
        content += f'<div class="card"><div class="card-num">{roas:.2f}x</div><div class="card-label">ROAS</div></div>'
        content += '</div>'
    
    with open(output, 'w', encoding='utf-8') as f:
        f.write(content)
    print(f"✅ 周报: {output}")
    return output

def generate_monthly_report(month_str):
    """Generate a monthly report by aggregating daily reports (YYYY-MM)"""
    ensure_dirs()
    output = f'{BASE_DIR}/monthly/{month_str}.html'
    if os.path.exists(output):
        print(f"⏭️ 月报已存在: {output}")
        return output
    
    year, month = month_str.split('-')
    days_in_month = 31 if int(month) in [1,3,5,7,8,10,12] else (30 if int(month) != 2 else 28)
    
    total_shopify_gross = 0
    total_shopify_net = 0
    total_shopify_refund = 0
    total_shopify_orders = 0
    total_amz_rev = 0
    total_amz_orders = 0
    total_ads_cost = 0
    total_ads_conv = 0
    all_products = defaultdict(lambda: {'qty': 0, 'revenue': 0})
    all_countries = defaultdict(int)
    
    for day in range(1, days_in_month + 1):
        day_str = f"{year}-{int(month):02d}-{day:02d}"
        try:
            shopify = dsr.fetch_shopify_sales(day_str)
            amazon = dsr.fetch_amazon_sales(day_str)
            ads = dsr.fetch_google_ads(day_str)
            
            total_shopify_gross += shopify['gross']
            total_shopify_net += shopify['net']
            total_shopify_refund += shopify['refund_amount']
            total_shopify_orders += shopify['orders']
            
            for wh, data in amazon.items():
                total_amz_rev += data.get('revenue', 0)
                total_amz_orders += data.get('orders', 0)
            
            total_ads_cost += ads.get('cost', 0)
            total_ads_conv += ads.get('conv_value', 0)
            
            for sku, d in shopify.get('by_product', {}).items():
                all_products[sku]['qty'] += d['qty']
                all_products[sku]['revenue'] += d['revenue']
            for c, n in shopify.get('by_country', {}).items():
                all_countries[c] += n
        except:
            pass
    
    content = f'<div class="meta">📅 {year}年{int(month)}月 ({days_in_month}天)</div>\n'
    
    total_net = total_shopify_net + total_amz_rev
    total_orders = total_shopify_orders + total_amz_orders
    
    content += '<div class="cards">'
    content += f'<div class="card total"><div class="card-num">${total_net:,.0f}</div><div class="card-label">全渠道净销售 ({total_orders}单)</div></div>'
    content += f'<div class="card blue"><div class="card-num">${total_shopify_net:,.0f}</div><div class="card-label">Shopify 净销售 ({total_shopify_orders}单)</div></div>'
    if total_shopify_refund > 0:
        content += f'<div class="card red"><div class="card-num">-${total_shopify_refund:,.0f}</div><div class="card-label">退款</div></div>'
    if total_amz_rev > 0:
        content += f'<div class="card orange"><div class="card-num">${total_amz_rev:,.0f}</div><div class="card-label">Amazon ({total_amz_orders}单)</div></div>'
    content += '</div>'
    
    # Top 10 products
    if all_products:
        content += '<h3>🏆 Shopify 热销 Top 10</h3>'
        content += '<table><tr><th>#</th><th>SKU</th><th>数量</th><th>金额</th><th>占比</th></tr>'
        top10 = sorted(all_products.items(), key=lambda x: -x[1]['revenue'])[:10]
        for i, (sku, d) in enumerate(top10, 1):
            pct = d['revenue'] / total_shopify_gross * 100 if total_shopify_gross > 0 else 0
            content += f'<tr><td>{i}</td><td><strong>{sku}</strong></td><td>{d["qty"]}件</td><td>${d["revenue"]:,.0f}</td><td>{pct:.0f}%</td></tr>'
        content += '</table>'
    
    if all_countries:
        content += '<h3>🌍 订单地区</h3><div class="tags">'
        for c, n in sorted(all_countries.items(), key=lambda x: -x[1]):
            content += f'<span class="tag">{c} ({n})</span>'
        content += '</div>'
    
    if total_ads_cost > 0:
        content += '<h3>📊 广告数据</h3><div class="cards">'
        content += f'<div class="card"><div class="card-num">${total_ads_cost:,.0f}</div><div class="card-label">广告花费</div></div>'
        content += f'<div class="card"><div class="card-num">${total_ads_conv:,.0f}</div><div class="card-label">转化价值</div></div>'
        roas = total_ads_conv / total_ads_cost if total_ads_cost > 0 else 0
        content += f'<div class="card"><div class="card-num">{roas:.2f}x</div><div class="card-label">ROAS</div></div>'
        content += '</div>'
    
    with open(output, 'w', encoding='utf-8') as f:
        f.write(content)
    print(f"✅ 月报: {output}")
    return output

def generate_main_page():
    ensure_dirs()
    
    daily_reports = get_available_reports('daily')
    weekly_reports = get_available_reports('weekly')
    monthly_reports = get_available_reports('monthly')
    latest_date = daily_reports[0] if daily_reports else ''
    
    latest_content = ''
    if latest_date:
        try:
            with open(f'{BASE_DIR}/daily/{latest_date}.html', 'r') as f:
                latest_content = f.read()
        except: pass
    
    date_obj = datetime.strptime(latest_date, '%Y-%m-%d') if latest_date else datetime.now()
    weekdays = ['周一','周二','周三','周四','周五','周六','周日']
    weekday = weekdays[date_obj.weekday()]
    display_date = f"{date_obj.year}年{date_obj.month}月{date_obj.day}日"
    
    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Chessnut 综合报告</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
:root{{--bg:#f8fafc;--bg2:#ffffff;--bg3:#f1f5f9;--text:#1e293b;--text2:#475569;--text3:#64748b;--border:#e2e8f0;--accent:#3b82f6}}
[data-theme="dark"]{{--bg:#0f172a;--bg2:#1e293b;--bg3:#334155;--text:#e2e8f0;--text2:#94a3b8;--text3:#64748b;--border:#334155;--accent:#3b82f6}}
body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:var(--bg);color:var(--text);padding:20px;max-width:900px;margin:0 auto}}
.header{{text-align:center;margin-bottom:16px;position:relative}}
.header h1{{font-size:22px}}
.header .sub{{color:var(--text2);font-size:14px;margin-top:4px}}
.theme-toggle{{position:absolute;right:0;top:0;background:var(--bg2);border:1px solid var(--border);border-radius:8px;padding:6px 10px;cursor:pointer;font-size:16px}}
.top-bar{{display:flex;justify-content:space-between;align-items:center;margin-bottom:16px;flex-wrap:wrap;gap:8px}}
.type-tabs{{display:flex;gap:4px}}
.type-tab{{background:var(--bg2);border:1px solid var(--border);padding:8px 20px;border-radius:8px;text-decoration:none;color:var(--text2);font-size:14px;font-weight:600;cursor:pointer}}
.type-tab.active{{background:var(--accent);color:#fff;border-color:var(--accent)}}
.date-picker{{display:flex;align-items:center;gap:8px}}
.date-picker label{{font-size:14px;color:var(--text2)}}
.date-picker input{{background:var(--bg2);border:1px solid var(--border);border-radius:8px;padding:8px 12px;font-size:14px;color:var(--text);font-family:inherit}}
.back{{display:inline-block;margin-bottom:12px;color:var(--accent);text-decoration:none;font-size:14px}}
h3{{color:var(--text);margin:20px 0 10px;font-size:16px}}h3.red{{color:#f87171}}
.cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px;margin-bottom:16px}}
.card{{background:var(--bg2);border:1px solid var(--border);border-radius:10px;padding:14px;text-align:center}}
.card-num{{font-size:24px;font-weight:700}}.card-label{{font-size:12px;color:var(--text2);margin-top:4px}}
.card.total{{border-color:var(--accent)}}.card.total .card-num{{color:var(--accent)}}
.card.blue .card-num{{color:#3b82f6}}.card.green .card-num{{color:#22c55e}}.card.red .card-num{{color:#ef4444}}.card.orange .card-num{{color:#f97316}}
table{{width:100%;border-collapse:collapse;background:var(--bg2);border:1px solid var(--border);border-radius:10px;overflow:hidden;margin-bottom:16px}}
th{{background:var(--bg3);padding:8px 10px;text-align:left;font-size:12px;color:var(--text2);font-weight:600}}
td{{padding:7px 10px;border-bottom:1px solid var(--border);font-size:13px}}tr:last-child td{{border-bottom:none}}
.bar-container{{width:50px;background:var(--bg3);border-radius:4px;height:6px;display:inline-block}}
.bar{{height:6px;border-radius:4px}}.bar-danger{{background:#ef4444}}.bar-warning{{background:#f59e0b}}
.days-cell{{display:flex;align-items:center;gap:6px}}
.tags{{display:flex;flex-wrap:wrap;gap:8px}}.tag{{background:var(--bg3);padding:4px 10px;border-radius:6px;font-size:13px}}
.alert-card{{background:var(--bg2);border-left:3px solid #f87171;padding:10px 14px;margin-bottom:8px;border-radius:0 8px 8px 0}}
.alert-title{{font-weight:600;margin-bottom:4px;font-size:14px}}.alert-desc{{font-size:12px;color:var(--text2)}}
.meta{{font-size:13px;color:var(--text2);margin-bottom:8px}}
.footer{{text-align:center;color:var(--text3);font-size:12px;margin-top:24px}}
#report-content{{min-height:200px}}
.loading{{text-align:center;padding:40px;color:var(--text2)}}
[data-theme="dark"] .date-picker input{{color-scheme:dark}}
@media(prefers-color-scheme:dark){{:root:not([data-theme="light"]){{--bg:#0f172a;--bg2:#1e293b;--bg3:#334155;--text:#e2e8f0;--text2:#94a3b8;--text3:#64748b;--border:#334155}}}}
</style>
</head>
<body>
<a class="back" href="/dashboard">← 库存仪表盘</a>
<div class="header">
    <h1>📊 Chessnut 综合报告</h1>
    <div class="sub" id="report-date">{display_date}（{weekday}）</div>
    <button class="theme-toggle" onclick="toggleTheme()">🌓</button>
</div>
<div class="top-bar">
    <div class="type-tabs">
        <button class="type-tab active" onclick="switchTab('daily')">日报</button>
        <button class="type-tab" onclick="switchTab('weekly')">周报</button>
        <button class="type-tab" onclick="switchTab('monthly')">月报</button>
    </div>
    <div class="date-picker" id="date-picker-area">
        <input type="date" id="date-picker-daily" value="{latest_date}" onchange="loadDaily(this.value)">
        <input type="week" id="date-picker-weekly" style="display:none" onchange="loadWeekly(this.value)">
        <input type="month" id="date-picker-monthly" style="display:none" onchange="loadMonthly(this.value)">
    </div>
</div>
<div id="report-content">
{latest_content}
</div>
<div class="footer">Chessnut 综合报告系统 • 每日 00:00 自动更新</div>
<script>
const availableDaily = {json.dumps(daily_reports)};
const availableWeekly = {json.dumps(weekly_reports)};
const availableMonthly = {json.dumps(monthly_reports)};

function switchTab(tab) {{
    document.querySelectorAll('.type-tab').forEach(t => t.classList.remove('active'));
    event.target.classList.add('active');
    
    document.getElementById('date-picker-daily').style.display = 'none';
    document.getElementById('date-picker-weekly').style.display = 'none';
    document.getElementById('date-picker-monthly').style.display = 'none';
    
    const content = document.getElementById('report-content');
    const dateLabel = document.getElementById('report-date');
    
    if (tab === 'daily') {{
        document.getElementById('date-picker-daily').style.display = 'block';
        if (availableDaily.length > 0) {{
            loadDaily(availableDaily[0]);
        }} else {{
            content.innerHTML = '<div class="loading">暂无日报数据</div>';
            dateLabel.textContent = '';
        }}
    }} else if (tab === 'weekly') {{
        document.getElementById('date-picker-weekly').style.display = 'block';
        if (availableWeekly.length > 0) {{
            loadWeekly(availableWeekly[0]);
        }} else {{
            content.innerHTML = '<div class="loading">暂无周报数据</div>';
            dateLabel.textContent = '';
        }}
    }} else if (tab === 'monthly') {{
        document.getElementById('date-picker-monthly').style.display = 'block';
        if (availableMonthly.length > 0) {{
            loadMonthly(availableMonthly[0]);
        }} else {{
            content.innerHTML = '<div class="loading">暂无月报数据</div>';
            dateLabel.textContent = '';
        }}
    }}
}}

function loadDaily(date) {{
    if (!date) return;
    const content = document.getElementById('report-content');
    const dateLabel = document.getElementById('report-date');
    content.innerHTML = '<div class="loading">加载中...</div>';
    const d = new Date(date + 'T00:00:00');
    const weekdays = ['周日','周一','周二','周三','周四','周五','周六'];
    dateLabel.textContent = `${{d.getFullYear()}}年${{d.getMonth()+1}}月${{d.getDate()}}日（${{weekdays[d.getDay()]}}）`;
    fetch(`/reports/daily/${{date}}.html`)
        .then(r => r.ok ? r.text() : Promise.reject())
        .then(html => content.innerHTML = html)
        .catch(() => content.innerHTML = '<div class="loading">该日期无报告数据</div>');
}}

function loadWeekly(week) {{
    if (!week) return;
    const content = document.getElementById('report-content');
    const dateLabel = document.getElementById('report-date');
    content.innerHTML = '<div class="loading">加载中...</div>';
    const [year, w] = week.split('-W');
    dateLabel.textContent = `${{year}}年 第${{parseInt(w)}}周`;
    fetch(`/reports/weekly/${{week}}.html`)
        .then(r => r.ok ? r.text() : Promise.reject())
        .then(html => content.innerHTML = html)
        .catch(() => content.innerHTML = '<div class="loading">该周无报告数据</div>');
}}

function loadMonthly(month) {{
    if (!month) return;
    const content = document.getElementById('report-content');
    const dateLabel = document.getElementById('report-date');
    content.innerHTML = '<div class="loading">加载中...</div>';
    const [year, m] = month.split('-');
    dateLabel.textContent = `${{year}}年${{parseInt(m)}}月`;
    fetch(`/reports/monthly/${{month}}.html`)
        .then(r => r.ok ? r.text() : Promise.reject())
        .then(html => content.innerHTML = html)
        .catch(() => content.innerHTML = '<div class="loading">该月无报告数据</div>');
}}

function toggleTheme() {{
    const b = document.documentElement;
    const c = b.getAttribute('data-theme');
    const n = c === 'dark' ? 'light' : 'dark';
    b.setAttribute('data-theme', n);
    localStorage.setItem('theme', n);
}}

(function() {{
    const s = localStorage.getItem('theme');
    if (s) document.documentElement.setAttribute('data-theme', s);
    else if (window.matchMedia('(prefers-color-scheme:dark)').matches) document.documentElement.setAttribute('data-theme', 'dark');
}})();
</script>
</body></html>"""
    
    with open(MAIN_HTML, 'w', encoding='utf-8') as f:
        f.write(html)
    print(f"✅ 主页: {MAIN_HTML}")

if __name__ == '__main__':
    if len(sys.argv) > 1:
        if sys.argv[1] == 'index':
            generate_main_page()
        else:
            generate_daily_report(sys.argv[1])
    else:
        generate_daily_report()
        generate_main_page()
