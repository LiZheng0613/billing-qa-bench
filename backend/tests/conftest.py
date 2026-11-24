"""pytest 公共配置：确保能导入 billing_qa 包。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture(autouse=True)
def _fresh_model_cache():
    """每个测试前后清空模型注册的执行期快照缓存。

    测试经常直接改库翻转模型启用状态（不走 API，不触发失效钩子），
    短 TTL 缓存会把上一个测试的模型状态泄漏进下一个测试。
    """
    from billing_qa.services.llm import registry

    registry.invalidate_model_cache()
    yield
    registry.invalidate_model_cache()
