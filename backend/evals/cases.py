"""评测用例定义与金标集加载。

金标用例存 evals/golden/*.json，人工审校维护；`python -m evals extract`
可从存量真实轨迹生成骨架供审校。工具名一律用 trace 内部名。
"""
import json
from dataclasses import dataclass, field
from pathlib import Path

GOLDEN_DIR = Path(__file__).parent / "golden"


@dataclass
class EvalCase:
    id: str
    question: str
    must_call: list = field(default_factory=list)
    one_of: list = field(default_factory=list)       # [[组内任一], ...]
    must_not_call: list = field(default_factory=list)
    max_tool_calls: int = 0                          # 0 = 用运行时配置的上限
    answer_rules: list = field(default_factory=list) # 空 = DEFAULT_RULES
    notes: str = ""

    @classmethod
    def from_dict(cls, d: dict) -> "EvalCase":
        known = {k: v for k, v in d.items() if k in cls.__dataclass_fields__}
        return cls(**known)


def load_golden(directory: Path = GOLDEN_DIR) -> list[EvalCase]:
    cases = []
    for p in sorted(directory.glob("*.json")):
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
        items = data if isinstance(data, list) else [data]
        cases.extend(EvalCase.from_dict(d) for d in items)
    ids = [c.id for c in cases]
    dupes = {i for i in ids if ids.count(i) > 1}
    if dupes:
        raise ValueError(f"金标用例 id 重复: {sorted(dupes)}")
    return cases
