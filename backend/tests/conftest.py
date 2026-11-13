"""pytest 公共配置：确保能导入 billing_qa 包。"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
