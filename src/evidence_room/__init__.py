"""受控证据室领域模型。

按"调查假设 -> 取证授权 -> 材料登记 -> 印证分析 -> 阶段结论"的脉络
组织对象关系；所有决定（升级、补授权、失效、借阅、复核、结论……）
都以只增不改的事件形式记入哈希链日志，投影据此重建当前状态。
"""

from .types import (
    MaterialCategory,
    DecisionKind,
    CaseStage,
    Team,
    LinkStatus,
    LeadStatus,
    AccessScope,
)
from .log import append_event, verify_chain
from .room import EvidenceRoom, RuleViolation

__all__ = [
    "MaterialCategory",
    "DecisionKind",
    "CaseStage",
    "Team",
    "LinkStatus",
    "LeadStatus",
    "AccessScope",
    "append_event",
    "verify_chain",
    "EvidenceRoom",
    "RuleViolation",
]
