"""内置 Mock：被测「电信计费智能问账客服」（SSE + 白盒字段 + Pre-Hook 鉴权）。

覆盖电信计费领域常见问答：账单总额、费用构成、套餐余量与超套、国际漫游、
增值业务、欠费停机、电子发票、融合宽带/IPTV、积分兑换，以及歧义/越权/
不可回答等非清晰输入行为。

启动：uvicorn mock_servers.target_api:app --port 8002
"""

from __future__ import annotations

import asyncio
import json
import re
import uuid
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

app = FastAPI(title="Mock Telecom Billing Q&A Target", version="2.0.0")

# ---------------------------------------------------------------------- #
# 预制电信计费数据（账期冻结，保证可复现）
# ---------------------------------------------------------------------- #
SUBSCRIBERS: dict[str, dict[str, Any]] = {
    "13800138000": {
        "phone": "13800138000",
        "account_no": "CTC20260001001",
        "customer": "陈晓峰",
        "city": "杭州",
        "package_name": "5G畅享套餐199元档",
        "monthly_fee": 199.00,
        "bill_period": "2026-10",
        "bill_total": 268.50,
        "bill_items": [
            {"name": "套餐月费", "amount": 199.00},
            {"name": "国内流量超套费", "amount": 27.00},
            {"name": "国际漫游费", "amount": 32.50},
            {"name": "增值业务费（彩铃）", "amount": 10.00},
        ],
        "data_total_gb": 60,
        "data_used_gb": 69,
        "data_overage_fee": 27.00,
        "voice_total_min": 1000,
        "voice_used_min": 860,
        "sms_total": 100,
        "sms_used": 20,
        "vas": [{"name": "彩铃", "status": "已订购", "monthly_fee": 10.00}],
        "roaming": {
            "destination": "日本",
            "start": "2026-10-12",
            "end": "2026-10-15",
            "data_mb": 2450,
            "fee": 32.50,
        },
        "arrears": 0.00,
        "service_status": "正常",
        "unpaid_bills": [],
        "invoice_amount": 268.50,
        "invoice_status": "可开具",
        "points": 12800,
        "broadband": None,
        "sub_cards": [],
        "contract_until": "2027-03-31",
        "balance_available": 86.50,
        "balance_frozen": 0.00,
        "balance_grant": 20.00,
        "balance_grant_expire": "2026-12-31",
        "ordered_offers": [
            {
                "offer_id": "OFF-5G-199",
                "effective_date": "2026-01-01",
                "expire_date": "2027-03-31",
                "contract_until": "2027-03-31",
            },
            {
                "offer_id": "OFF-CRBT",
                "effective_date": "2025-06-01",
                "expire_date": None,
                "contract_until": None,
            },
        ],
    },
    "13900139000": {
        "phone": "13900139000",
        "account_no": "CTC20260002001",
        "customer": "李婉清",
        "city": "上海",
        "package_name": "全家享融合套餐239元档",
        "monthly_fee": 239.00,
        "bill_period": "2026-10",
        "bill_total": 356.80,
        "bill_items": [
            {"name": "融合套餐月费", "amount": 239.00},
            {"name": "IPTV增值包", "amount": 20.00},
            {"name": "国内流量超套费", "amount": 45.00},
            {"name": "副卡功能费", "amount": 30.00},
            {"name": "视频会员", "amount": 22.80},
        ],
        "data_total_gb": 60,
        "data_used_gb": 75,
        "data_overage_fee": 45.00,
        "voice_total_min": 1500,
        "voice_used_min": 1200,
        "sms_total": 200,
        "sms_used": 36,
        "vas": [
            {"name": "视频会员", "status": "已订购", "monthly_fee": 22.80},
            {"name": "IPTV增值包", "status": "已订购", "monthly_fee": 20.00},
        ],
        "roaming": None,
        "arrears": 0.00,
        "service_status": "正常",
        "unpaid_bills": [],
        "invoice_amount": 356.80,
        "invoice_status": "可开具",
        "points": 23600,
        "broadband": {"name": "1000M光纤宽带", "monthly_fee": 0.00, "status": "正常"},
        "sub_cards": ["13900139001", "13900139002"],
        "contract_until": "2027-08-31",
        "balance_available": 320.00,
        "balance_frozen": 50.00,
        "balance_grant": 0.00,
        "balance_grant_expire": None,
        "ordered_offers": [
            {
                "offer_id": "OFF-FAMILY-239",
                "effective_date": "2026-01-01",
                "expire_date": "2027-08-31",
                "contract_until": "2027-08-31",
            },
            {
                "offer_id": "OFF-IPTV",
                "effective_date": "2026-03-01",
                "expire_date": None,
                "contract_until": None,
            },
            {
                "offer_id": "OFF-VIDEO",
                "effective_date": "2026-05-01",
                "expire_date": None,
                "contract_until": None,
            },
        ],
    },
    "13700137000": {
        "phone": "13700137000",
        "account_no": "CTC20260003001",
        "customer": "王建国",
        "city": "成都",
        "package_name": "4G飞享套餐59元档",
        "monthly_fee": 59.00,
        "bill_period": "2026-09",
        "bill_total": 59.00,
        "bill_items": [{"name": "套餐月费", "amount": 59.00}],
        "data_total_gb": 10,
        "data_used_gb": 8.6,
        "data_overage_fee": 0.00,
        "voice_total_min": 200,
        "voice_used_min": 120,
        "sms_total": 50,
        "sms_used": 5,
        "vas": [],
        "roaming": None,
        "arrears": 118.00,
        "service_status": "欠费停机",
        "unpaid_bills": [
            {"period": "2026-08", "amount": 59.00},
            {"period": "2026-09", "amount": 59.00},
        ],
        "invoice_amount": 0.00,
        "invoice_status": "欠费不可开具",
        "points": 300,
        "broadband": None,
        "sub_cards": [],
        "contract_until": "2026-12-31",
        "balance_available": 0.00,
        "balance_frozen": 0.00,
        "balance_grant": 0.00,
        "balance_grant_expire": None,
        "ordered_offers": [
            {
                "offer_id": "OFF-4G-59",
                "effective_date": "2025-01-01",
                "expire_date": "2026-12-31",
                "contract_until": "2026-12-31",
            }
        ],
    },
    "13600136000": {
        "phone": "13600136000",
        "account_no": "CTC20260004001",
        "customer": "赵敏",
        "city": "深圳",
        "package_name": "5G畅享套餐129元档",
        "monthly_fee": 129.00,
        "bill_period": "2026-10",
        "bill_total": 129.00,
        "bill_items": [{"name": "套餐月费", "amount": 129.00}],
        "data_total_gb": 30,
        "data_used_gb": 18,
        "data_overage_fee": 0.00,
        "voice_total_min": 500,
        "voice_used_min": 210,
        "sms_total": 100,
        "sms_used": 12,
        "vas": [],
        "roaming": None,
        "arrears": 0.00,
        "service_status": "正常",
        "unpaid_bills": [],
        "invoice_amount": 129.00,
        "invoice_status": "可开具",
        "points": 8600,
        "broadband": {"name": "300M光纤宽带", "monthly_fee": 30.00, "status": "正常"},
        "sub_cards": [],
        "contract_until": "2026-12-31",
        "balance_available": 45.60,
        "balance_frozen": 0.00,
        "balance_grant": 10.00,
        "balance_grant_expire": "2026-11-30",
        "ordered_offers": [
            {
                "offer_id": "OFF-5G-129",
                "effective_date": "2025-06-01",
                "expire_date": "2026-12-31",
                "contract_until": "2026-12-31",
            },
            {
                "offer_id": "OFF-BB-300",
                "effective_date": "2025-06-01",
                "expire_date": None,
                "contract_until": None,
            },
        ],
    },
    "13500135000": {
        "phone": "13500135000",
        "account_no": "CTC20260005001",
        "customer": "周海涛",
        "city": "北京",
        "package_name": "全球通尊享套餐399元档",
        "monthly_fee": 399.00,
        "bill_period": "2026-10",
        "bill_total": 468.00,
        "bill_items": [
            {"name": "套餐月费", "amount": 399.00},
            {"name": "国际漫游费", "amount": 69.00},
        ],
        "data_total_gb": 100,
        "data_used_gb": 96,
        "data_overage_fee": 0.00,
        "voice_total_min": 2000,
        "voice_used_min": 1500,
        "sms_total": 300,
        "sms_used": 40,
        "vas": [],
        "roaming": {
            "destination": "韩国",
            "start": "2026-10-03",
            "end": "2026-10-06",
            "data_mb": 1260,
            "fee": 69.00,
        },
        "arrears": 0.00,
        "service_status": "正常",
        "unpaid_bills": [],
        "invoice_amount": 468.00,
        "invoice_status": "可开具",
        "points": 56000,
        "broadband": {"name": "1000M光纤宽带", "monthly_fee": 0.00, "status": "正常"},
        "sub_cards": [],
        "contract_until": "2028-01-31",
        "balance_available": 1200.00,
        "balance_frozen": 0.00,
        "balance_grant": 0.00,
        "balance_grant_expire": None,
        "ordered_offers": [
            {
                "offer_id": "OFF-GLOBAL-399",
                "effective_date": "2025-01-01",
                "expire_date": "2028-01-31",
                "contract_until": "2028-01-31",
            },
            {
                "offer_id": "OFF-BB-1000",
                "effective_date": "2025-01-01",
                "expire_date": None,
                "contract_until": None,
            },
        ],
    },
}

# offer 目录（主套餐 / 宽带 / 增值业务 / 可加装包），月费与优惠价均为已冻结口径
OFFER_CATALOG: dict[str, dict[str, Any]] = {
    "OFF-5G-199": {
        "name": "5G畅享套餐199元档",
        "category": "主套餐",
        "monthly_fee": 199.00,
        "discount_fee": 199.00,
        "contract_months": 24,
    },
    "OFF-FAMILY-239": {
        "name": "全家享融合套餐239元档",
        "category": "主套餐",
        "monthly_fee": 239.00,
        "discount_fee": 239.00,
        "contract_months": 24,
    },
    "OFF-4G-59": {
        "name": "4G飞享套餐59元档",
        "category": "主套餐",
        "monthly_fee": 59.00,
        "discount_fee": 59.00,
        "contract_months": 12,
    },
    "OFF-5G-129": {
        "name": "5G畅享套餐129元档",
        "category": "主套餐",
        "monthly_fee": 129.00,
        "discount_fee": 129.00,
        "contract_months": 12,
    },
    "OFF-GLOBAL-399": {
        "name": "全球通尊享套餐399元档",
        "category": "主套餐",
        "monthly_fee": 399.00,
        "discount_fee": 399.00,
        "contract_months": 36,
    },
    "OFF-CRBT": {
        "name": "彩铃",
        "category": "增值业务",
        "monthly_fee": 10.00,
        "discount_fee": 10.00,
        "contract_months": 0,
    },
    "OFF-IPTV": {
        "name": "IPTV增值包",
        "category": "增值业务",
        "monthly_fee": 20.00,
        "discount_fee": 20.00,
        "contract_months": 0,
    },
    "OFF-VIDEO": {
        "name": "视频会员",
        "category": "增值业务",
        "monthly_fee": 22.80,
        "discount_fee": 18.00,
        "contract_months": 0,
    },
    "OFF-BB-300": {
        "name": "300M光纤宽带",
        "category": "宽带",
        "monthly_fee": 30.00,
        "discount_fee": 30.00,
        "contract_months": 0,
    },
    "OFF-BB-1000": {
        "name": "1000M光纤宽带",
        "category": "宽带",
        "monthly_fee": 0.00,
        "discount_fee": 0.00,
        "contract_months": 0,
    },
    "OFF-DATA-20": {
        "name": "20GB流量加油包",
        "category": "流量包",
        "monthly_fee": 20.00,
        "discount_fee": 15.00,
        "contract_months": 0,
    },
    "OFF-VOICE-300": {
        "name": "300分钟语音包",
        "category": "语音包",
        "monthly_fee": 15.00,
        "discount_fee": 12.00,
        "contract_months": 0,
    },
    "OFF-5G-UP": {
        "name": "5G极速升级包",
        "category": "网络权益",
        "monthly_fee": 30.00,
        "discount_fee": 24.00,
        "contract_months": 12,
    },
    "OFF-ROAM-ASIA": {
        "name": "亚洲漫游日包",
        "category": "漫游包",
        "monthly_fee": 25.00,
        "discount_fee": 20.00,
        "contract_months": 0,
    },
}

# 可加装 offer（“可订购”列表只列这些；主套餐/宽带/已订购增值业务不重复列）
AVAILABLE_OFFER_IDS = ("OFF-DATA-20", "OFF-VOICE-300", "OFF-5G-UP", "OFF-ROAM-ASIA")

# 兼容历史订单域（旧演示数据 / 测试脚本仍可能引用）
ORDERS: dict[str, dict[str, Any]] = {
    "A2024001": {
        "order_no": "A2024001",
        "customer": "杭州云启科技有限公司",
        "period": "2026-10",
        "total_amount": 12345.67,
        "item_count": 3,
        "items": [
            {"sku": "CLOUD-ENT-01", "name": "企业云服务年费", "amount": 8000.00},
            {"sku": "SUPPORT-PRO", "name": "高级技术支持", "amount": 3000.00},
            {"sku": "TRAINING-01", "name": "培训服务", "amount": 1345.67},
        ],
    },
    "A2024002": {
        "order_no": "A2024002",
        "customer": "上海衡远贸易有限公司",
        "period": "2026-09",
        "total_amount": 5678.90,
        "item_count": 2,
        "items": [
            {"sku": "CLOUD-STD-01", "name": "标准云服务", "amount": 4678.90},
            {"sku": "SUPPORT-STD", "name": "标准支持", "amount": 1000.00},
        ],
    },
}

_PHONE_PATTERN = re.compile(r"1[3-9]\d{9}")
_ACCOUNT_PATTERN = re.compile(r"CTC\d{8,}")
_ORDER_PATTERN = re.compile(r"[A-Z]\d{7}")
_PERIOD_PATTERN = re.compile(r"(20\d{2})\s*[-/年]\s*(\d{1,2})")
_QUESTION_WORDS = (
    "查",
    "金额",
    "账",
    "账单",
    "话费",
    "费用",
    "明细",
    "发票",
    "退款",
    "套餐",
    "流量",
    "余量",
    "超套",
    "欠费",
    "停机",
    "复机",
    "宽带",
    "积分",
    "漫游",
    "增值",
    "业务",
    "offer",
    "余额",
    "订购",
    "优惠",
    "合约",
)
_SESSIONS: dict[str, str] = {}

_INTENT_LABELS = {
    "BILL": "账单查询",
    "USAGE": "套餐余量/超套查询",
    "ROAMING": "国际漫游费查询",
    "VAS": "增值业务查询",
    "ARREARS": "欠费停机查询",
    "INVOICE": "电子发票查询",
    "BROADBAND": "宽带/IPTV查询",
    "POINTS": "积分查询",
    "BALANCE": "账户余额查询",
    "OFFER": "offer 订购查询",
    "CLARIFY": "澄清追问",
    "REFUSE": "礼貌拒绝",
    "MULTI_INTENT": "多意图查询",
    "CHITCHAT": "闲聊引导",
}


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": "mock-telecom-billing-target"}


@app.post("/api/auth/token")
async def issue_token() -> dict[str, Any]:
    await asyncio.sleep(0.01)
    return {"code": 0, "message": "success", "data": {"token": "mock-agent-token-2026"}}


def _money(value: float) -> str:
    return f"{value:,.2f}"


def _history_texts(history: Any) -> list[str]:
    if not isinstance(history, list):
        return []
    texts: list[str] = []
    for item in reversed(history):
        content = str((item or {}).get("content") or "")
        if content:
            texts.append(content)
    return texts


def _extract_period(text: str) -> tuple[str, str, bool] | None:
    match = _PERIOD_PATTERN.search(text or "")
    if match is None:
        return None
    year, month = int(match.group(1)), int(match.group(2))
    return f"{year:04d}-{month:02d}", match.group(0), 1 <= month <= 12


def _resolve_subscriber(query: str, history: Any, session_id: str) -> dict[str, Any] | None:
    for text in [query, *_history_texts(history), _SESSIONS.get(session_id, "")]:
        if not text:
            continue
        phone_match = _PHONE_PATTERN.search(text)
        if phone_match and phone_match.group(0) in SUBSCRIBERS:
            return SUBSCRIBERS[phone_match.group(0)]
        account_match = _ACCOUNT_PATTERN.search(text)
        if account_match:
            for subscriber in SUBSCRIBERS.values():
                if subscriber["account_no"] == account_match.group(0):
                    return subscriber
    return None


def _resolve_order(query: str, history: Any, session_id: str) -> dict[str, Any] | None:
    for text in [query, *_history_texts(history), _SESSIONS.get(session_id, "")]:
        match = _ORDER_PATTERN.search(text or "")
        if match and match.group(0) in ORDERS:
            return ORDERS[match.group(0)]
    return None


def _classify_intent(query: str) -> str:
    text = query or ""
    if any(
        word in text
        for word in (
            "offer",
            "可订购",
            "能办理",
            "可办理",
            "折扣",
            "资费方案",
            "活动",
            "合约",
        )
    ) or ("优惠" in text and "增值" not in text):
        return "OFFER"
    if any(word in text for word in ("增值", "彩铃", "视频会员", "退订", "会员")):
        return "VAS"
    if "订购" in text:
        return "OFFER"
    if any(word in text for word in ("漫游", "国际", "境外", "出国")):
        return "ROAMING"
    if any(word in text for word in ("欠费", "停机", "复机", "滞纳金")):
        return "ARREARS"
    if any(word in text for word in ("发票", "开票")):
        return "INVOICE"
    if any(word in text.lower() for word in ("宽带", "iptv", "网速")):
        return "BROADBAND"
    if any(word in text for word in ("积分", "兑换")):
        return "POINTS"
    usage_words = ("套餐", "余量", "剩余", "流量", "语音", "分钟", "短信", "超套")
    if any(word in text for word in ("余额", "预存", "冻结")) and not any(
        word in text for word in usage_words
    ):
        return "BALANCE"
    if any(word in text for word in usage_words):
        return "USAGE"
    return "BILL"


def _sql_for(intent: str) -> str:
    templates = {
        "BILL": (
            "SELECT phone_no, period, SUM(amount) AS bill_total, COUNT(*) AS item_count "
            "FROM telecom_bill_item WHERE phone_no = $phone AND period = $period GROUP BY 1,2;"
        ),
        "USAGE": (
            "SELECT phone_no, data_used_gb, data_total_gb, voice_used_min, sms_used "
            "FROM telecom_package_usage WHERE phone_no = $phone AND period = $period;"
        ),
        "ROAMING": (
            "SELECT phone_no, destination, roam_start, roam_end, data_mb, fee "
            "FROM telecom_roaming_detail WHERE phone_no = $phone AND period = $period;"
        ),
        "VAS": (
            "SELECT phone_no, service_name, status, monthly_fee "
            "FROM telecom_vas_order WHERE phone_no = $phone;"
        ),
        "ARREARS": (
            "SELECT phone_no, SUM(amount) AS arrears, MAX(service_status) AS status "
            "FROM telecom_arrears WHERE phone_no = $phone GROUP BY phone_no;"
        ),
        "INVOICE": (
            "SELECT phone_no, period, invoice_amount, invoice_status "
            "FROM telecom_invoice WHERE phone_no = $phone AND period = $period;"
        ),
        "BROADBAND": (
            "SELECT phone_no, product_name, monthly_fee, status "
            "FROM telecom_broadband WHERE phone_no = $phone;"
        ),
        "POINTS": "SELECT phone_no, available_points FROM telecom_points WHERE phone_no = $phone;",
        "BALANCE": (
            "SELECT phone_no, available_balance, frozen_balance "
            "FROM telecom_account_balance WHERE phone_no = $phone;"
        ),
        "OFFER": (
            "SELECT phone_no, offer_id, offer_name, monthly_fee, discount_fee, status "
            "FROM telecom_offer_order WHERE phone_no = $phone;"
        ),
    }
    return templates.get(intent, "")


def _build_bill(sub: dict[str, Any], period: str, query: str) -> dict[str, Any]:
    total = float(sub["bill_total"])
    if "错误金额" in query:
        total = 999.00
    items = sub["bill_items"]
    item_text = "、".join(f"{item['name']} ¥{_money(float(item['amount']))}" for item in items)
    if "json" in query.lower():
        # 演示英文 key 名称绑定：最终答案直接返回结构化 JSON。
        answer = json.dumps(
            {"total_amount": round(total, 2), "item_count": len(items)},
            ensure_ascii=False,
        )
    else:
        answer = (
            f"手机号 {sub['phone']}（{sub['customer']}）{period} 账单总额为 "
            f"¥{_money(total)}，共 {len(items)} 项费用：{item_text}。"
        )
    return {
        "answer": answer,
        "intent": "BILL",
        "generated_sql": _sql_for("BILL"),
        "api_params": {"phone_no": sub["phone"], "period": period},
        "raw_db_data": {
            "query_type": "BILL",
            "phone_no": sub["phone"],
            "customer": sub["customer"],
            "period": period,
            "package_name": sub["package_name"],
            "bill_total": total,
            "item_count": len(items),
            "items": items,
        },
    }


def _build_usage(sub: dict[str, Any], period: str) -> dict[str, Any]:
    used = float(sub["data_used_gb"])
    total = float(sub["data_total_gb"])
    overage = round(max(0.0, used - total), 2)
    remaining = round(max(0.0, total - used), 2)
    voice_remaining = max(0, int(sub["voice_total_min"]) - int(sub["voice_used_min"]))
    sms_remaining = max(0, int(sub["sms_total"]) - int(sub["sms_used"]))
    answer = (
        f"手机号 {sub['phone']} 的 {sub['package_name']} 套餐：国内流量总量 "
        f"{total:g}GB，已使用 {used:g}GB，剩余 {remaining:g}GB，超套 {overage:g}GB，"
        f"超套费 ¥{_money(float(sub['data_overage_fee']))}；国内语音总量 "
        f"{sub['voice_total_min']} 分钟，已使用 {sub['voice_used_min']} 分钟，"
        f"剩余 {voice_remaining} 分钟；短信总量 {sub['sms_total']} 条，"
        f"已使用 {sub['sms_used']} 条，剩余 {sms_remaining} 条。"
    )
    return {
        "answer": answer,
        "intent": "USAGE",
        "generated_sql": _sql_for("USAGE"),
        "api_params": {"phone_no": sub["phone"], "period": period},
        "raw_db_data": {
            "query_type": "USAGE",
            "phone_no": sub["phone"],
            "period": period,
            "package_name": sub["package_name"],
            "data_total_gb": total,
            "data_used_gb": used,
            "data_overage_gb": overage,
            "data_overage_fee": float(sub["data_overage_fee"]),
            "voice_total_min": sub["voice_total_min"],
            "voice_used_min": sub["voice_used_min"],
            "sms_total": sub["sms_total"],
            "sms_used": sub["sms_used"],
        },
    }


def _build_roaming(sub: dict[str, Any], period: str) -> dict[str, Any]:
    roaming = sub.get("roaming")
    if not roaming:
        answer = f"手机号 {sub['phone']} 在 {period} 未产生国际漫游费用。"
        raw: Any = None
    else:
        answer = (
            f"手机号 {sub['phone']} 在 {period} 产生国际漫游费 "
            f"¥{_money(float(roaming['fee']))}，漫游时间为 {roaming['start']} 至 "
            f"{roaming['end']}，目的地 {roaming['destination']}，"
            f"漫游流量 {roaming['data_mb']}MB。"
        )
        raw = roaming
    return {
        "answer": answer,
        "intent": "ROAMING",
        "generated_sql": _sql_for("ROAMING"),
        "api_params": {"phone_no": sub["phone"], "period": period},
        "raw_db_data": {
            "query_type": "ROAMING",
            "phone_no": sub["phone"],
            "period": period,
            "roaming": raw,
        },
    }


def _build_vas(sub: dict[str, Any], period: str) -> dict[str, Any]:
    active = [item for item in sub["vas"] if item["status"] == "已订购"]
    if active:
        detail = "、".join(
            f"{item['name']}（¥{_money(float(item['monthly_fee']))}/月）" for item in active
        )
        answer = f"手机号 {sub['phone']} 在 {period} 已订购增值业务 {len(active)} 项：{detail}。"
    else:
        answer = f"手机号 {sub['phone']} 在 {period} 未订购任何收费增值业务。"
    return {
        "answer": answer,
        "intent": "VAS",
        "generated_sql": _sql_for("VAS"),
        "api_params": {"phone_no": sub["phone"]},
        "raw_db_data": {
            "query_type": "VAS",
            "phone_no": sub["phone"],
            "period": period,
            "items": sub["vas"],
        },
    }


def _build_arrears(sub: dict[str, Any]) -> dict[str, Any]:
    arrears = float(sub["arrears"])
    if arrears <= 0:
        answer = (
            f"手机号 {sub['phone']}（{sub['customer']}）当前无欠费，"
            f"服务状态为{sub['service_status']}。"
        )
    else:
        unpaid = sub["unpaid_bills"]
        unpaid_text = "、".join(
            f"{item['period']} 账单 ¥{_money(float(item['amount']))}" for item in unpaid
        )
        answer = (
            f"手机号 {sub['phone']}（{sub['customer']}）当前欠费 "
            f"¥{_money(arrears)}，共 {len(unpaid)} 期账单未结清：{unpaid_text}；"
            f"服务状态为{sub['service_status']}。"
        )
    return {
        "answer": answer,
        "intent": "ARREARS",
        "generated_sql": _sql_for("ARREARS"),
        "api_params": {"phone_no": sub["phone"]},
        "raw_db_data": {
            "query_type": "ARREARS",
            "phone_no": sub["phone"],
            "customer": sub["customer"],
            "arrears": arrears,
            "service_status": sub["service_status"],
            "unpaid_bills": sub["unpaid_bills"],
        },
    }


def _build_invoice(sub: dict[str, Any], period: str) -> dict[str, Any]:
    answer = (
        f"手机号 {sub['phone']} 在 {period} 可开具电子发票金额为 "
        f"¥{_money(float(sub['invoice_amount']))}，已开票金额 ¥0.00，"
        f"发票状态为{sub['invoice_status']}。"
    )
    return {
        "answer": answer,
        "intent": "INVOICE",
        "generated_sql": _sql_for("INVOICE"),
        "api_params": {"phone_no": sub["phone"], "period": period},
        "raw_db_data": {
            "query_type": "INVOICE",
            "phone_no": sub["phone"],
            "period": period,
            "invoice_amount": float(sub["invoice_amount"]),
            "invoice_status": sub["invoice_status"],
        },
    }


def _build_broadband(sub: dict[str, Any], period: str) -> dict[str, Any]:
    broadband = sub.get("broadband")
    if not broadband:
        answer = f"手机号 {sub['phone']} 当前未办理宽带业务。"
        raw: Any = None
    else:
        iptv = next((item for item in sub["vas"] if "IPTV" in item["name"]), None)
        extras = f"，IPTV增值包月费 ¥{_money(float(iptv['monthly_fee']))}" if iptv else ""
        answer = (
            f"手机号 {sub['phone']} 的宽带为 {broadband['name']}，宽带月费 "
            f"¥{_money(float(broadband['monthly_fee']))}，当前状态{broadband['status']}；"
            f"融合套餐月费为 ¥{_money(float(sub['monthly_fee']))}{extras}，"
            f"账期 {period}。"
        )
        raw = broadband
    return {
        "answer": answer,
        "intent": "BROADBAND",
        "generated_sql": _sql_for("BROADBAND"),
        "api_params": {"phone_no": sub["phone"]},
        "raw_db_data": {
            "query_type": "BROADBAND",
            "phone_no": sub["phone"],
            "period": period,
            "broadband": raw,
        },
    }


def _build_points(sub: dict[str, Any]) -> dict[str, Any]:
    answer = (
        f"手机号 {sub['phone']} 当前可用积分 {sub['points']} 分，可兑换话费、流量包或视频会员权益。"
    )
    return {
        "answer": answer,
        "intent": "POINTS",
        "generated_sql": _sql_for("POINTS"),
        "api_params": {"phone_no": sub["phone"]},
        "raw_db_data": {
            "query_type": "POINTS",
            "phone_no": sub["phone"],
            "available_points": sub["points"],
        },
    }


def _offer_item_text(offer_id: str, ordered: dict[str, Any] | None = None) -> str:
    offer = OFFER_CATALOG[offer_id]
    fee = float(ordered["monthly_fee"]) if ordered and "monthly_fee" in ordered else float(
        offer["monthly_fee"]
    )
    discount = float(offer["discount_fee"])
    parts = [f"月费 ¥{_money(fee)}"]
    if discount != fee:
        parts.append(f"优惠价 ¥{_money(discount)}")
    if int(offer["contract_months"]) > 0:
        parts.append(f"合约 {int(offer['contract_months'])} 个月")
    if ordered:
        parts.append(f"{ordered['effective_date']} 生效")
        contract_until = ordered.get("contract_until")
        parts.append(
            f"合约至 {contract_until}" if contract_until else "无固定合约期"
        )
    return f"{offer['name']}（{'，'.join(parts)}）"


def _build_balance(sub: dict[str, Any]) -> dict[str, Any]:
    available = float(sub["balance_available"])
    frozen = float(sub["balance_frozen"])
    total = round(available + frozen, 2)
    answer = (
        f"手机号 {sub['phone']} 的账户可用余额为 ¥{_money(available)}，"
        f"冻结余额 ¥{_money(frozen)}，合计余额 ¥{_money(total)}"
    )
    grant = float(sub["balance_grant"])
    if grant > 0 and sub["balance_grant_expire"]:
        answer += f"；赠费 ¥{_money(grant)} 将于 {sub['balance_grant_expire']} 到期"
    arrears = float(sub["arrears"])
    if arrears > 0:
        answer += (
            f"；欠费金额 ¥{_money(arrears)}，当前处于{sub['service_status']}状态"
        )
    answer += "。"
    return {
        "answer": answer,
        "intent": "BALANCE",
        "generated_sql": _sql_for("BALANCE"),
        "api_params": {"phone_no": sub["phone"]},
        "raw_db_data": {
            "query_type": "BALANCE",
            "phone_no": sub["phone"],
            "available_balance": available,
            "frozen_balance": frozen,
            "total_balance": total,
            "grant_balance": grant,
            "grant_expire": sub["balance_grant_expire"],
            "arrears": arrears,
            "service_status": sub["service_status"],
        },
    }


def _is_ordered_offer_query(query: str) -> bool:
    return any(
        word in query
        for word in (
            "已订购",
            "订购了",
            "我订购",
            "我的",
            "合约到",
            "合约至",
            "合约期",
            "到期时间",
            "到期日",
            "生效时间",
            "生效日",
        )
    )


def _build_offer(sub: dict[str, Any], query: str) -> dict[str, Any]:
    ordered_mode = _is_ordered_offer_query(query)
    if ordered_mode:
        items = [_offer_item_text(item["offer_id"], item) for item in sub["ordered_offers"]]
        answer = (
            f"手机号 {sub['phone']} 当前已订购 {len(items)} 个 offer："
            + "、".join(items)
            + "。"
        )
        raw_items = [
            {
                "offer_id": item["offer_id"],
                **OFFER_CATALOG[item["offer_id"]],
                "effective_date": item["effective_date"],
                "contract_until": item["contract_until"],
                "status": "已生效",
            }
            for item in sub["ordered_offers"]
        ]
        status = "ORDERED"
    else:
        ordered_ids = {item["offer_id"] for item in sub["ordered_offers"]}
        offer_ids = [item for item in AVAILABLE_OFFER_IDS if item not in ordered_ids]
        items = [_offer_item_text(item) for item in offer_ids]
        answer = (
            f"手机号 {sub['phone']} 当前可订购 {len(items)} 个 offer："
            + "、".join(items)
            + "。"
        )
        raw_items = [{"offer_id": item, **OFFER_CATALOG[item]} for item in offer_ids]
        status = "AVAILABLE"
    return {
        "answer": answer,
        "intent": "OFFER",
        "generated_sql": _sql_for("OFFER"),
        "api_params": {"phone_no": sub["phone"], "mode": status},
        "raw_db_data": {
            "query_type": "OFFER",
            "phone_no": sub["phone"],
            "mode": status,
            "items": raw_items,
        },
    }


def _build_telecom_answer(
    sub: dict[str, Any], period: str, intent: str, query: str
) -> dict[str, Any]:
    if intent == "USAGE":
        return _build_usage(sub, period)
    if intent == "ROAMING":
        return _build_roaming(sub, period)
    if intent == "VAS":
        return _build_vas(sub, period)
    if intent == "ARREARS":
        return _build_arrears(sub)
    if intent == "INVOICE":
        return _build_invoice(sub, period)
    if intent == "BROADBAND":
        return _build_broadband(sub, period)
    if intent == "POINTS":
        return _build_points(sub)
    if intent == "BALANCE":
        return _build_balance(sub)
    if intent == "OFFER":
        return _build_offer(sub, query)
    return _build_bill(sub, period, query)


def _build_order_answer(query: str, order: dict[str, Any] | None) -> dict[str, Any]:
    if order is None:
        return {
            "answer": "为了准确核对账单，请提供具体的手机号、订单号或账期，我再帮您查询明细。",
            "intent": "CLARIFY",
            "generated_sql": None,
            "api_params": {"query": query},
            "raw_db_data": None,
        }
    amount = f"{order['total_amount']:,.2f}"
    # 故障注入：命中「错误金额」时故意返回错误数字，用于验证规则硬校验
    if "错误金额" in query:
        amount = "999.00"
        answer = (
            f"订单 {order['order_no']} 的含税金额为 ¥{amount}，共 {order['item_count']} 笔明细。"
        )
    else:
        answer = (
            f"订单 {order['order_no']}（{order['period']}）的含税金额为 ¥{amount}，"
            f"共 {order['item_count']} 笔明细，客户为{order['customer']}。"
        )
    return {
        "answer": answer,
        "intent": "BILLING_QUERY",
        "generated_sql": (
            "SELECT order_no, SUM(amount) AS total_amount, COUNT(*) AS item_count "
            f"FROM billing_order_item WHERE order_no = '{order['order_no']}' "
            f"AND period = '{order['period']}' GROUP BY order_no;"
        ),
        "api_params": {"order_no": order["order_no"], "period": order["period"]},
        "raw_db_data": order,
    }


def _clarify_result(query: str, *, vague: bool = False) -> dict[str, Any]:
    if vague:
        answer = (
            "请问您要查询哪一个手机号码的哪个月份的账单？"
            "您更关注套餐月费、流量超套费，还是增值业务费？"
        )
    else:
        answer = (
            "请问您要查询哪一个手机号码或宽带账号的本月账单？您是想看总额、费用明细，还是套餐余量？"
        )
    return {
        "answer": answer,
        "intent": "CLARIFY",
        "generated_sql": None,
        "api_params": {"query": query},
        "raw_db_data": None,
    }


def _refuse_result(query: str, *, reason: str) -> dict[str, Any]:
    return {
        "answer": reason,
        "intent": "REFUSE",
        "generated_sql": None,
        "api_params": {"query": query},
        "raw_db_data": None,
    }


def _multi_bill_result(phones: list[str], period: str | None) -> dict[str, Any]:
    parts: list[str] = []
    rows: list[dict[str, Any]] = []
    for phone in phones[:2]:
        sub = SUBSCRIBERS[phone]
        bill_period = period or sub["bill_period"]
        parts.append(f"{phone} 在 {bill_period} 的账单总额为 ¥{_money(float(sub['bill_total']))}")
        rows.append(
            {
                "phone_no": phone,
                "period": bill_period,
                "bill_total": float(sub["bill_total"]),
            }
        )
    answer = (
        "仅为您分别查询到两条账单："
        + "；".join(parts)
        + "。如需逐项明细，请告知优先查看哪一个号码。"
    )
    return {
        "answer": answer,
        "intent": "MULTI_INTENT",
        "generated_sql": _sql_for("BILL"),
        "api_params": {"phone_nos": phones[:2], "period": period},
        "raw_db_data": {"query_type": "MULTI_INTENT", "bills": rows},
    }


def _assumption_bill_result(sub: dict[str, Any]) -> dict[str, Any]:
    items = sub["bill_items"]
    answer = (
        f"按最近一期账期 {sub['bill_period']} 口径，手机号 {sub['phone']} 的账单总额为 "
        f"¥{_money(float(sub['bill_total']))}，共 {len(items)} 项费用。"
    )
    return {
        "answer": answer,
        "intent": "BILL",
        "generated_sql": _sql_for("BILL"),
        "api_params": {"phone_no": sub["phone"], "period": sub["bill_period"]},
        "raw_db_data": {
            "query_type": "BILL",
            "phone_no": sub["phone"],
            "period": sub["bill_period"],
            "bill_total": float(sub["bill_total"]),
            "item_count": len(items),
            "items": items,
            "assumption": f"用户未指定账期，按最近一期 {sub['bill_period']} 口径",
        },
    }


def _resolve_result(
    query: str, history: Any, session_id: str, subscriber: dict[str, Any] | None
) -> dict[str, Any]:
    # 越权 / 不可回答 / 矛盾账期优先拦截
    if any(word in query for word in ("同事", "别人", "他人", "非本人")) and any(
        word in query for word in ("账单", "明细", "消费")
    ):
        return _refuse_result(
            query,
            reason=(
                "抱歉，出于用户隐私保护，我无权查询或提供他人号码的账单明细，"
                "也无法代发到非绑定邮箱。"
            ),
        )
    if any(word in query for word in ("火星", "外星", "不存在号码")):
        return _refuse_result(
            query,
            reason=(
                "抱歉，当前系统无法查询火星基地或不存在号码的通信账单与套餐余量，"
                "建议您核实号码后再试。"
            ),
        )

    period_info = _extract_period(query)
    if period_info is not None and not period_info[2]:
        return _refuse_result(
            query,
            reason=(
                f"{period_info[1]} 不是有效账期，无法按该账期查询。"
                "请问您要查询的是 2026-10 还是 2026-11？"
            ),
        )

    phones = list(dict.fromkeys(_PHONE_PATTERN.findall(query)))
    known_phones = [phone for phone in phones if phone in SUBSCRIBERS]
    if len(known_phones) >= 2:
        period = period_info[0] if period_info else None
        return _multi_bill_result(known_phones, period)

    if subscriber is None:
        if any(word in query for word in _QUESTION_WORDS):
            vague = any(word in query for word in ("这么贵", "怎么回事", "为什么"))
            return _clarify_result(query, vague=vague)
        return {
            "answer": "我主要负责电信账单、套餐余量、漫游费与欠费查询，您可以告诉我想查询的手机号或账期。",
            "intent": "CHITCHAT",
            "generated_sql": None,
            "api_params": None,
            "raw_db_data": None,
        }

    period = period_info[0] if period_info else subscriber["bill_period"]
    intent = _classify_intent(query)
    if (
        period_info is None
        and intent in {"BILL", "INVOICE"}
        and any(word in query for word in ("最近", "本月", "这个月"))
    ):
        return _assumption_bill_result(subscriber)
    return _build_telecom_answer(subscriber, period, intent, query)


def _chunks(text: str, size: int = 6) -> list[str]:
    return [text[index : index + size] for index in range(0, len(text), size)]


async def _sse_stream(result: dict[str, Any]):
    metadata = {
        "type": "metadata",
        "session_id": result["session_id"],
        "intent": result["intent"],
        "intent_label": _INTENT_LABELS.get(result["intent"], result["intent"]),
        "generated_sql": result["generated_sql"],
        "api_params": result["api_params"],
        "raw_db_data": result["raw_db_data"],
    }
    yield f"event: metadata\ndata: {json.dumps(metadata, ensure_ascii=False)}\n\n"
    await asyncio.sleep(0.08)
    for chunk in _chunks(result["answer"]):
        frame = {"choices": [{"index": 0, "delta": {"content": chunk}}]}
        yield f"data: {json.dumps(frame, ensure_ascii=False)}\n\n"
        await asyncio.sleep(0.015)
    yield "data: [DONE]\n\n"


@app.post("/api/v1/chat")
@app.post("/api/chat")
async def chat(request: Request):
    try:
        body = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"invalid json: {exc}") from exc

    query = str(body.get("query") or "")
    session_id = str(body.get("session_id") or "") or f"sess-{uuid.uuid4().hex[:12]}"
    history = body.get("history") or []

    if "模拟崩溃" in query or "触发异常" in query:
        raise HTTPException(status_code=500, detail="mock internal error injected")

    subscriber = _resolve_subscriber(query, history, session_id)
    order = _resolve_order(query, history, session_id)

    if subscriber is not None:
        _SESSIONS[session_id] = subscriber["phone"]
        result = _resolve_result(query, history, session_id, subscriber)
    elif order is not None:
        _SESSIONS[session_id] = order["order_no"]
        result = _build_order_answer(query, order)
    else:
        result = _resolve_result(query, history, session_id, None)
    result = dict(result)
    result["session_id"] = session_id

    accept = request.headers.get("accept", "")
    if "text/event-stream" in accept:
        return StreamingResponse(_sse_stream(result), media_type="text/event-stream")
    await asyncio.sleep(0.05)
    return JSONResponse(
        {"code": 0, "message": "success", "data": result},
        headers={"X-Mock-Session": session_id},
    )
