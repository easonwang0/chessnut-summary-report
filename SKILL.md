---
name: chessnut-summary-report
description: |
  Chessnut 综合报告系统 - 每日自动生成销售、库存、广告、舆情综合报告
  包含 Shopify/Amazon 销售数据、库存预警、海运在途、Google Ads ROAS、品牌舆情监控
---

# Chessnut 综合报告系统

## 功能

- **日报**: 每日自动生成，包含销售、库存、广告、舆情全维度数据
- **周报**: 每周日自动汇总过去7天数据
- **月报**: 每月1日自动汇总上月数据
- **Web 页面**: 独立页面展示，支持日期选择器浏览历史报告
- **每日自动更新**: 通过 cron 任务静默执行

## 数据源

| 数据 | 来源 | 脚本 |
|------|------|------|
| Shopify 销售 | Shopify Admin API | `daily_sales_report.py` |
| Amazon 销售 | Amazon SP-API (Orders) | `daily_sales_report.py` |
| Google Ads | Google Ads API | `daily_sales_report.py` |
| 库存数据 | 谷仓 API + Amazon Reports API | `inventory_dashboard_v2.py` |
| 海运在途 | 飞书 Bitable | `daily_sales_report.py` |
| 品牌舆情 | xiu agent 监控报告 | `daily_sales_report.py` |

## 文件结构

```
scripts/
  daily_sales_report.py    # 核心数据获取模块（Shopify/Amazon/Ads/舆情）
  sales_report_page.py     # Web 页面生成器（日报/周报/月报）
references/
  .amazon_credentials.json # Amazon SP-API 凭证
  .shopify_token           # Shopify API token
  .google_ads_credentials.json # Google Ads API 凭证
```

## 依赖

- Python 3.10+
- `google-ads` Python 包（Google Ads API）
- 飞书 API（海运在途数据）

## 配置

凭证文件需放在 `references/` 目录下：
- `.shopify_token`: `{"shop": "xxx.myshopify.com", "access_token": "shpat_xxx"}`
- `.amazon_credentials.json`: Amazon SP-API 凭证
- `.google_ads_credentials.json`: Google Ads API 凭证

## 部署

```bash
# 复制脚本到工作目录
cp scripts/*.py /path/to/SHOPIFY/scripts/

# 复制凭证
cp references/.amazon_credentials.json /path/to/SHOPIFY/
cp references/.shopify_token /path/to/SHOPIFY/
cp references/.google_ads_credentials.json /path/to/SHOPIFY/

# 生成报告
python3 scripts/sales_report_page.py 2026-04-25  # 生成指定日期日报
python3 scripts/sales_report_page.py index        # 生成主页

# 设置 cron（静默执行，不推送）
# 每日 00:00
# 每周日 00:00
# 每月1日 00:00
```

## Web 部署

报告页面通过 Caddy 提供：
- 主页: `/sales-report` → `/var/www/dashboard/sales-report.html`
- 日报: `/reports/daily/YYYY-MM-DD.html`
- 周报: `/reports/weekly/YYYY-WXX.html`
- 月报: `/reports/monthly/YYYY-MM.html`

需要在 Caddyfile 中添加 basicauth 保护。
