"""证据室使用的枚举与常量。"""

from enum import StrEnum


class MaterialCategory(StrEnum):
    """材料类别：判断必须建立在多类材料相互印证之上。"""

    SETTLEMENT = "settlement"        # 历年医保结算记录
    SURVEILLANCE = "surveillance"    # 医院取药监控片段
    TRACEABILITY = "traceability"    # 药品追溯码流向
    FINANCE = "finance"              # 资金往来材料
    LOGISTICS = "logistics"          # 物流寄递材料
    AUTHORIZATION = "authorization"  # 取证授权文书
    OTHER = "other"


class DecisionKind(StrEnum):
    """决定日志中可能出现的事件类型。

    日志只增不改：即便材料失效、授权补充或对象合并，旧事件也原样保留，
    只追加新的决定事件来说明状态变化。
    """

    CASE_OPENED = "case_opened"
    HYPOTHESIS_REGISTERED = "hypothesis_registered"
    SUBJECT_REGISTERED = "subject_registered"
    LEAD_ESCALATED = "lead_escalated"
    AUTHORIZATION_GRANTED = "authorization_granted"
    MATERIAL_REGISTERED = "material_registered"
    CANDIDATE_LINK_PROPOSED = "candidate_link_proposed"
    IDENTITY_REVIEWED = "identity_reviewed"
    MATERIAL_INVALIDATED = "material_invalidated"
    ACCESS_GRANTED = "access_granted"
    PARALLEL_ANALYSIS = "parallel_analysis"
    STAGE_CONCLUSION_SUBMITTED = "stage_conclusion_submitted"
    MATERIAL_TRANSFERRED = "material_transferred"


class CaseStage(StrEnum):
    """案件阶段，顺序即阶段门控顺序。"""

    INITIAL = "initial"        # 线索初查
    CORROBORATION = "corroboration"  # 多源印证
    CASE_FILING = "case_filing"      # 立案核查
    PROSECUTION_REVIEW = "prosecution_review"  # 移送审查


# 阶段在门控序列中的次序
STAGE_ORDER = {stage: i for i, stage in enumerate(CaseStage)}


class Team(StrEnum):
    """并行办案小组。各小组只能看到履职所需部分材料。"""

    LEAD = "lead"                  # 线索组
    SURVEILLANCE = "surveillance"  # 监控组
    FUND_FLOW = "fund_flow"        # 资金组
    LOGISTICS = "logistics"        # 物流组
    REVIEW = "review"              # 负责人 / 复核


class LinkStatus(StrEnum):
    """跨材料候选对应（如视频片段与结算记录）的复核状态。"""

    CANDIDATE = "candidate"  # 候选：算法或人工初判，未经复核
    CONFIRMED = "confirmed"  # 已复核确认同一对象/同一事实
    REJECTED = "rejected"    # 复核排除


class LeadStatus(StrEnum):
    """线索（调查假设）状态。"""

    PENDING = "pending"        # 待查
    ESCALATED = "escalated"    # 已升级为正式调查方向
    CLOSED = "closed"          # 查否或了结


class AccessScope(StrEnum):
    """材料可使用范围。"""

    CASE_TEAM = "case_team"  # 办案组内按授权使用
    LOANED = "loaned"        # 已跨部门借阅，借阅期内借入方可使用
    SEALED = "sealed"        # 已随阶段结论冻结，停止外借
