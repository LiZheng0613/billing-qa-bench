"""REST API 冒烟：覆盖鉴权、环境、模型、Prompt、用例审计、任务控制与 Trace 查询。

用法（backend 目录，需先启动 8000/8001/8002 三个服务）：
    python scripts/api_smoke.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import httpx  # noqa: E402 - 需先调整 sys.path 才能导入项目模块

BASE = "http://127.0.0.1:8000/api/v1"
PASSED: list[str] = []
FAILED: list[str] = []


def check(condition: bool, label: str, detail: str = "") -> None:
    if condition:
        PASSED.append(label)
        print(f"[OK]   {label}")
    else:
        FAILED.append(f"{label} {detail}")
        print(f"[FAIL] {label} {detail}")


def wait_task(client: httpx.Client, task_id: int, timeout: float = 120.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        task = client.get(f"/tasks/{task_id}").json()
        if task["status"] in {"COMPLETED", "ABORTED", "FAILED"}:
            return task
        time.sleep(1)
    raise TimeoutError(f"任务 {task_id} 超时未结束")


def main() -> int:
    client = httpx.Client(base_url=BASE, timeout=180)

    # 1. 鉴权
    response = client.post("/auth/login", json={"username": "admin", "password": "admin123"})
    check(response.status_code == 200, "登录成功", response.text[:200])
    token = response.json()["access_token"]
    client.headers.update({"Authorization": f"Bearer {token}"})
    check(client.get("/auth/me").json()["username"] == "admin", "当前用户信息")

    # 2. 环境
    envs = client.get("/environments").json()
    check(envs["total"] >= 2, "环境列表")
    env = next(item for item in envs["items"] if item["code"] == "QA-MOCK")
    vars_resp = client.get(f"/environments/{env['id']}/variables")
    check(vars_resp.status_code == 200, "环境变量池")
    check(any(item["value_masked"].count("*") > 0 for item in vars_resp.json() if item["is_secret"]), "敏感变量掩码")
    health = client.post(f"/environments/{env['id']}/health-check").json()
    check(health.get("ok") is True, "环境探活", str(health))

    agent_env = next(item for item in envs["items"] if item["code"] == "QA-MOCK-JSON")
    check(agent_env.get("api_path") == "/api/v1/agent/chat", "Agent 报文环境接口")
    check((agent_env.get("basic_auth") or {}).get("password_set") is True, "Agent 环境 Basic 鉴权")
    check(bool((agent_env.get("pre_hook") or {}).get("enabled")), "Agent 环境前置接口")

    # 3. 模型
    models = client.get("/models").json()
    check(models["total"] >= 3, "模型列表")
    judge = next(item for item in models["items"] if item["code"] == "MOCK-JUDGE")
    model_test = client.post(f"/models/{judge['id']}/test").json()
    check(model_test["ok"] is True, "模型连通性测试", str(model_test)[:200])

    # 4. Prompt 资产
    stamp = int(time.time())
    prompts = client.get("/prompts").json()
    check(prompts["total"] >= 2, "内置模板种子")
    sim_prompt = next(item for item in prompts["items"] if item["code"] == "SIM_DEFAULT_BILLING_USER")
    detail = client.get(f"/prompts/{sim_prompt['id']}").json()
    check(detail["current_version"] >= 1 and detail["versions"], "模板版本详情")
    usage = client.get(f"/prompts/{sim_prompt['id']}/usage").json()
    check(usage["ref_count"] >= 1 and not usage["can_delete"], "模板引用影响面")
    # 版本发布在独立临时模板上验证，避免污染内置模板内容
    probe_resp = client.post(
        "/prompts",
        json={
            "code": f"API-SMOKE-TPL-{stamp}",
            "name": "API 冒烟临时模板",
            "template_type": "USER_SIMULATOR",
            "description": "仅用于 API 冒烟，结束后自动删除",
            "content": "你是一名业务用户，请围绕 {{user_goal}} 提问；当前第 {{current_turn}} 轮。",
            "changelog": "冒烟临时模板初始版本",
        },
    )
    check(probe_resp.status_code == 200, "临时模板创建")
    probe = probe_resp.json()
    check(len(probe["versions"]) == 1, "新建模板版本历史即时可用")
    version_resp = client.post(
        f"/prompts/{probe['id']}/versions",
        json={
            "content": probe["versions"][0]["content"] + "\n# 冒烟追加版本\n请保持输出合法 JSON。",
            "changelog": "API 冒烟新增版本",
        },
    )
    version_body = version_resp.json()
    check(
        version_resp.status_code == 200
        and version_body["current_version"] == probe["current_version"] + 1,
        "发布新版本",
    )
    check(len(version_body["versions"]) == 2, "发布后版本历史即时刷新")
    check(client.delete(f"/prompts/{probe['id']}").status_code == 200, "临时模板清理")

    # 5. 用例 CRUD + 版本审计
    cases = client.get("/cases", params={"page_size": 200}).json()
    check(cases["total"] >= 5, "用例列表")
    case = next(item for item in cases["items"] if item["code"] == "DEMO-SINGLE-001")
    case_detail = client.get(f"/cases/{case['id']}").json()
    check(case_detail["case_type"] == "SINGLE", "用例详情")
    revisions = client.get(f"/cases/{case['id']}/revisions").json()
    check(len(revisions) >= 1, "修改记录")
    diff = client.get(
        f"/cases/{case['id']}/revisions/diff",
        params={"from_version": revisions[-1]["version"], "to_version": revisions[0]["version"]},
    ).json()
    check(diff["total"] >= 0, "版本 Diff")

    created = client.post(
        "/cases",
        json={
            "code": f"API-SMOKE-{stamp}",
            "name": "API 冒烟用例",
            "case_type": "SINGLE",
            "priority": "P2",
            "tags": ["冒烟"],
            "environment_id": env["id"],
            "query": "订单 A2024001 的含税金额是多少？",
            "expected_answer": "含税金额 ¥12,345.67，共 3 笔明细",
            "expected_rules": {
                "amounts": [{"label": "含税金额", "value": 12345.67}],
                "counts": [{"label": "笔数", "value": 3}],
            },
            "judge_strategy": "AVG",
            "judge_model_ids": [judge["id"]],
        },
    )
    check(created.status_code == 200, "创建用例", created.text[:300])
    created_case = created.json()
    updated = client.put(
        f"/cases/{created_case['id']}",
        json={"name": "API 冒烟用例(已改名)", "max_turns": 6, "change_summary": "改名并调整轮次"},
    )
    check(updated.status_code == 200 and updated.json()["current_version"] == 2, "编辑生成 v2")
    diff2 = client.get(
        f"/cases/{created_case['id']}/revisions/diff",
        params={"from_version": 1, "to_version": 2},
    ).json()
    check(any(change["path"] == "name" for change in diff2["changes"]), "字段级 Diff 命中 name")
    rollback = client.post(f"/cases/{created_case['id']}/rollback", json={"version": 1})
    check(
        rollback.status_code == 200
        and rollback.json()["current_version"] == 3
        and rollback.json()["name"] == "API 冒烟用例",
        "一键回滚至 v1",
        rollback.text[:300],
    )

    # 6. 单条调试
    debug = client.post(f"/cases/{created_case['id']}/debug", json={})
    check(
        debug.status_code == 200 and len(debug.json()["turns"]) >= 1,
        "单条用例调试",
        debug.text[:300],
    )
    debug_exec = debug.json()
    check(bool(debug_exec["judges"]), "调试生成裁判明细")

    # 7. 沙盒试跑（裁判模式，复用历史快照）
    sandbox = client.post(
        f"/prompts/{sim_prompt['id']}/sandbox",
        json={
            "mode": "JUDGE",
            "execution_id": debug_exec["id"],
            "variables": {"case_type": "SINGLE"},
        },
    )
    # 模板类型不匹配时应报 4xx，这里验证校验生效
    check(sandbox.status_code in {400, 422}, "沙盒模板类型校验", sandbox.text[:200])
    judge_prompt = next(item for item in prompts["items"] if item["code"] == "JUDGE_DEFAULT_ZERO_TOLERANCE")
    sandbox2 = client.post(
        f"/prompts/{judge_prompt['id']}/sandbox",
        json={"mode": "JUDGE", "execution_id": debug_exec["id"]},
    )
    check(
        sandbox2.status_code == 200 and sandbox2.json()["output"] is not None,
        "沙盒裁判试跑",
        sandbox2.text[:300],
    )

    # 8. 批量任务 + 实时指标
    task_resp = client.post(
        "/tasks",
        json={
            "name": f"API 冒烟任务 {stamp}",
            "environment_id": env["id"],
            "case_ids": [case["id"], next(i["id"] for i in cases["items"] if i["code"] == "DEMO-SINGLE-FAIL")],
            "concurrency": 2,
            "qps_limit": 10,
            "retry_on_error": True,
            "max_error_retries": 2,
            "retry_backoff_seconds": 2,
        },
    )
    check(task_resp.status_code == 200, "创建批量任务", task_resp.text[:300])
    created_task = task_resp.json()
    check(
        created_task["retry_on_error"] is True
        and created_task["max_error_retries"] == 2
        and created_task["retry_backoff_seconds"] == 2,
        "任务异常重试配置回显",
        str(created_task)[:300],
    )
    task = wait_task(client, task_resp.json()["id"])
    check(task["status"] == "COMPLETED", "任务完成", str(task))
    metrics = client.get(f"/tasks/{task['id']}/metrics").json()
    check(
        metrics["finished_cases"] == 2 and metrics["total_tokens"] > 0,
        "任务指标聚合",
        str(metrics)[:300],
    )
    check(metrics["retry_count"] == 0, "正常任务无自动重试", str(metrics)[:300])
    task_execs = client.get(f"/tasks/{task['id']}/executions").json()
    check(len(task_execs["items"]) == 2, "任务执行明细")
    check(
        all(item["attempt_no"] == 1 for item in task_execs["items"]),
        "执行明细包含尝试次数",
        str(task_execs["items"])[:300],
    )
    detail_exec = client.get(f"/executions/{task_execs['items'][0]['id']}").json()
    check(
        bool(detail_exec["turns"]) and bool(detail_exec["case_snapshot"]),
        "全链路 Trace",
        str(detail_exec)[:200],
    )
    raw = client.get(f"/executions/{detail_exec['id']}/raw").json()
    check(bool(raw["turns"]) and raw["turns"][0]["request"] is not None, "原始报文")
    history = client.get(f"/cases/{case['id']}/executions").json()
    check(history["total"] >= 1, "用例历史执行记录")
    trend = client.get(f"/cases/{case['id']}/trend").json()
    check(len(trend["points"]) >= 1, "用例趋势聚合")

    # 9. 任务终止
    active_cases = [item for item in cases["items"] if item["status"] == "ACTIVE"]
    case_ids = [item["id"] for item in active_cases[:5]]
    control_task = client.post(
        "/tasks",
        json={
            "name": f"控制面任务 {stamp}",
            "environment_id": env["id"],
            "case_ids": case_ids,
            "concurrency": 1,
            "qps_limit": 3,
            "auto_start": False,
        },
    ).json()
    client.post(f"/tasks/{control_task['id']}/start")
    time.sleep(0.6)
    abort = client.post(f"/tasks/{control_task['id']}/abort")
    check(abort.status_code == 200, "终止任务", abort.text[:200])
    aborted = wait_task(client, control_task["id"], timeout=60)
    check(aborted["status"] == "ABORTED", "任务状态为 ABORTED", str(aborted["status"]))

    # 10. 断点重跑
    retry = client.post(f"/tasks/{aborted['id']}/retry-failed")
    check(retry.status_code == 200, "失败断点重跑", retry.text[:200])
    retried = wait_task(client, aborted["id"])
    check(retried["status"] == "COMPLETED", "重跑后任务完成", str(retried["status"]))

    # 清理
    client.delete(f"/cases/{created_case['id']}")

    print(f"\n通过 {len(PASSED)} 项，失败 {len(FAILED)} 项")
    if FAILED:
        for item in FAILED:
            print(" -", item)
        return 1
    print("API 冒烟全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
