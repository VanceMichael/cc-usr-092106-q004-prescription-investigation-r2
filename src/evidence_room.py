"""受控证据室：异常开药调查的材料登记、权限控制与阶段结论。

设计要点：

- 材料按调查假设登记，记录取证授权、来源、取得时间、校验摘要与可使用范围；
- 视频片段与结算记录等材料之间只能先建立“候选对应”，未经复核不得合并身份；
- 线索升级、补充授权、证据失效、跨部门借阅与并行分析全部写入仅可追加、
  哈希链式校验的决定日志，任何条目不可覆盖；
- 案情未到相应阶段时，不同小组只能看到履职所需的材料；
- 负责人提交阶段结论时冻结所用材料版本，报告可说明哪些事实已相互印证、
  哪些仍只是待核关联，以及每份材料后来被谁移交。

本模块只处理虚构的调查材料元数据，不包含真实个人信息。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Iterable, Optional


# ---------------------------------------------------------------------------
# 异常


class EvidenceRoomError(Exception):
    """证据室操作的基础异常。"""


class NotFoundError(EvidenceRoomError):
    """引用的案件、材料、授权或对应关系不存在。"""


class AuthorizationError(EvidenceRoomError):
    """缺少取证授权或操作超出授权、可使用范围。"""


class StageError(EvidenceRoomError):
    """阶段流转不合法（如倒退或越级回退）。"""


class ReviewError(EvidenceRoomError):
    """候选对应的复核不合法（如自提自审或重复复核）。"""


class InvalidStateError(EvidenceRoomError):
    """对象当前状态不允许该操作（如证据已失效、案件已结案）。"""


# ---------------------------------------------------------------------------
# 阶段与决定类型


class CaseStage(Enum):
    """案件阶段，只能沿顺序向前推进。"""

    LEAD = "线索核查"
    PRELIMINARY = "初步调查"
    FORMAL = "正式调查"
    CLOSED = "结案"


STAGE_ORDER = [
    CaseStage.LEAD,
    CaseStage.PRELIMINARY,
    CaseStage.FORMAL,
    CaseStage.CLOSED,
]


class DecisionKind(Enum):
    """写入决定日志的决定类型。"""

    AUTH_GRANT = "取证授权"
    SUPPLEMENTARY_AUTH = "补充授权"
    REGISTRATION = "材料登记"
    VERSION = "材料版本更新"
    RELATION = "对象关系登记"
    PROPOSAL = "候选对应"
    REVIEW = "对应复核"
    ESCALATION = "线索升级"
    INVALIDATION = "证据失效"
    BORROW = "跨部门借阅"
    ANALYSIS = "并行分析"
    CONCLUSION = "阶段结论"


class LinkStatus(Enum):
    """候选对应的复核状态。"""

    CANDIDATE = "待复核"
    CONFIRMED = "已确认"
    REJECTED = "已排除"


# ---------------------------------------------------------------------------
# 数据模型


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def content_digest(content: bytes) -> str:
    """材料的校验摘要。"""
    return hashlib.sha256(content).hexdigest()


@dataclass(frozen=True)
class Authorization:
    """取证授权：限定可取的来源与可使用范围。

    ``supplementary_of`` 指向同一案件的在先授权时表示补充授权，
    登记时会写入决定日志。
    """

    auth_id: str
    case_id: str
    granted_to: str
    sources: frozenset
    scope: frozenset
    valid_from: datetime
    valid_until: Optional[datetime] = None
    supplementary_of: Optional[str] = None

    def covers(self, source: str, scope: Iterable[str], at: datetime) -> bool:
        if source not in self.sources:
            return False
        if not set(scope).issubset(self.scope):
            return False
        if at < self.valid_from:
            return False
        if self.valid_until is not None and at > self.valid_until:
            return False
        return True


@dataclass(frozen=True)
class MaterialVersion:
    """材料的一个版本，按内容校验摘要区分。"""

    version: int
    digest: str
    registered_by: str
    at: datetime


@dataclass(frozen=True)
class Transfer:
    """一次移交/借阅记录，追加到材料的保管链上。"""

    department: str
    actor: str
    purpose: str
    at: datetime


@dataclass
class Material:
    """登记在案的调查材料。"""

    material_id: str
    case_id: str
    kind: str
    source: str
    acquired_at: datetime
    scope: frozenset
    authorization_id: str
    supports: frozenset = frozenset()
    versions: list = field(default_factory=list)
    custody: list = field(default_factory=list)
    invalidated: bool = False
    invalidation_reason: Optional[str] = None

    @property
    def current(self) -> MaterialVersion:
        return self.versions[-1]


@dataclass(frozen=True)
class Relation:
    """调查假设下登记的一条对象关系。"""

    hypothesis: str
    subject: str
    predicate: str
    obj: str
    actor: str
    at: datetime


@dataclass
class Correspondence:
    """两份材料之间的候选对应；复核通过前不得据此合并身份。"""

    link_id: str
    case_id: str
    left: str
    right: str
    note: str
    proposed_by: str
    at: datetime
    status: LinkStatus = LinkStatus.CANDIDATE
    reviewed_by: Optional[str] = None
    review_note: Optional[str] = None


@dataclass(frozen=True)
class Finding:
    """阶段结论中的一条事实认定及其依据材料。"""

    fact: str
    material_ids: tuple


@dataclass
class StageConclusion:
    """阶段结论：提交时冻结所用材料的版本。"""

    conclusion_id: str
    case_id: str
    stage: CaseStage
    submitted_by: str
    at: datetime
    findings: tuple
    frozen: dict  # material_id -> 冻结时的 MaterialVersion


@dataclass
class Case:
    case_id: str
    lead: str
    stage: CaseStage = CaseStage.LEAD
    relations: list = field(default_factory=list)


@dataclass(frozen=True)
class Decision:
    """决定日志条目：仅可追加，哈希链式衔接，不可覆盖。"""

    seq: int
    case_id: str
    kind: DecisionKind
    actor: str
    detail: dict
    at: datetime
    prev_hash: str
    hash: str


# ---------------------------------------------------------------------------
# 阶段化访问控制


# 小组 -> 阶段 -> 可见的材料种类；None 表示全部可见。
# 案情未到相应阶段时，小组只能看到履职所需的部分。
DEFAULT_ACCESS_POLICY = {
    "数据分析组": {
        CaseStage.LEAD: frozenset({"settlement", "trace"}),
        CaseStage.PRELIMINARY: frozenset({"settlement", "trace", "funds"}),
        CaseStage.FORMAL: None,
        CaseStage.CLOSED: None,
    },
    "外勤组": {
        CaseStage.LEAD: frozenset({"surveillance", "logistics"}),
        CaseStage.PRELIMINARY: frozenset({"surveillance", "logistics", "settlement"}),
        CaseStage.FORMAL: None,
        CaseStage.CLOSED: None,
    },
    "法制审核组": {
        CaseStage.LEAD: frozenset(),
        CaseStage.PRELIMINARY: frozenset(
            {"settlement", "surveillance", "trace", "funds", "logistics"}
        ),
        CaseStage.FORMAL: None,
        CaseStage.CLOSED: None,
    },
}


# ---------------------------------------------------------------------------
# 证据室


class EvidenceRoom:
    """受控证据室：一个案件一条决定链，材料、对应与结论均留痕。"""

    def __init__(self, access_policy: Optional[dict] = None):
        self._cases: dict = {}
        self._authorizations: dict = {}
        self._materials: dict = {}
        self._links: dict = {}
        self._conclusions: dict = {}
        self._log: list = []
        self._counters: dict = {}
        self._access_policy = access_policy or DEFAULT_ACCESS_POLICY

    # -- 内部工具 -----------------------------------------------------------

    def _next_id(self, case_id: str, prefix: str) -> str:
        key = (case_id, prefix)
        self._counters[key] = self._counters.get(key, 0) + 1
        return f"{case_id}-{prefix}{self._counters[key]:04d}"

    def _case(self, case_id: str) -> Case:
        try:
            return self._cases[case_id]
        except KeyError:
            raise NotFoundError(f"案件不存在：{case_id}") from None

    def _material(self, material_id: str) -> Material:
        try:
            return self._materials[material_id]
        except KeyError:
            raise NotFoundError(f"材料不存在：{material_id}") from None

    def _log_decision(
        self, case_id: str, kind: DecisionKind, actor: str, detail: dict, at: datetime
    ) -> Decision:
        seq = len(self._log)
        prev_hash = self._log[-1].hash if self._log else "GENESIS"
        payload = json.dumps(
            {
                "seq": seq,
                "case_id": case_id,
                "kind": kind.value,
                "actor": actor,
                "detail": detail,
                "at": at.isoformat(),
                "prev_hash": prev_hash,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        entry = Decision(
            seq=seq,
            case_id=case_id,
            kind=kind,
            actor=actor,
            detail=detail,
            at=at,
            prev_hash=prev_hash,
            hash=_sha256_text(payload),
        )
        self._log.append(entry)
        return entry

    # -- 案件与授权 ---------------------------------------------------------

    def open_case(self, case_id: str, lead: str) -> Case:
        if case_id in self._cases:
            raise InvalidStateError(f"案件已存在：{case_id}")
        case = Case(case_id=case_id, lead=lead)
        self._cases[case_id] = case
        return case

    def grant_authorization(self, auth: Authorization, actor: str,
                            at: Optional[datetime] = None) -> Authorization:
        """登记取证授权；指向在先授权时记为补充授权。"""
        self._case(auth.case_id)
        if auth.auth_id in self._authorizations:
            raise InvalidStateError(f"授权编号已存在：{auth.auth_id}")
        kind = DecisionKind.AUTH_GRANT
        if auth.supplementary_of is not None:
            base = self._authorizations.get(auth.supplementary_of)
            if base is None or base.case_id != auth.case_id:
                raise NotFoundError(
                    f"补充授权的在先授权不存在：{auth.supplementary_of}"
                )
            kind = DecisionKind.SUPPLEMENTARY_AUTH
        self._authorizations[auth.auth_id] = auth
        self._log_decision(
            auth.case_id,
            kind,
            actor,
            {
                "auth_id": auth.auth_id,
                "granted_to": auth.granted_to,
                "sources": sorted(auth.sources),
                "scope": sorted(auth.scope),
                "supplementary_of": auth.supplementary_of,
            },
            at or _utcnow(),
        )
        return auth

    # -- 材料登记与版本 ------------------------------------------------------

    def register_material(
        self,
        case_id: str,
        actor: str,
        kind: str,
        source: str,
        content: bytes,
        acquired_at: datetime,
        scope: Iterable[str],
        authorization_id: str,
        supports: Iterable[str] = (),
        at: Optional[datetime] = None,
    ) -> str:
        """登记材料：必须落在有效取证授权的来源与可使用范围内。"""
        case = self._case(case_id)
        if case.stage is CaseStage.CLOSED:
            raise InvalidStateError("案件已结案，不能再登记材料")
        auth = self._authorizations.get(authorization_id)
        if auth is None or auth.case_id != case_id:
            raise AuthorizationError(f"取证授权不存在：{authorization_id}")
        scope = frozenset(scope)
        if not auth.covers(source, scope, acquired_at):
            raise AuthorizationError(
                "材料来源、可使用范围或取得时间超出取证授权"
            )
        material_id = self._next_id(case_id, "M")
        registered_at = at or _utcnow()
        material = Material(
            material_id=material_id,
            case_id=case_id,
            kind=kind,
            source=source,
            acquired_at=acquired_at,
            scope=scope,
            authorization_id=authorization_id,
            supports=frozenset(supports),
        )
        material.versions.append(
            MaterialVersion(
                version=1,
                digest=content_digest(content),
                registered_by=actor,
                at=registered_at,
            )
        )
        self._materials[material_id] = material
        self._log_decision(
            case_id,
            DecisionKind.REGISTRATION,
            actor,
            {
                "material_id": material_id,
                "kind": kind,
                "source": source,
                "digest": material.current.digest,
                "authorization_id": authorization_id,
            },
            registered_at,
        )
        return material_id

    def update_material(
        self,
        material_id: str,
        actor: str,
        content: bytes,
        at: Optional[datetime] = None,
    ) -> MaterialVersion:
        """登记材料的新版本；已冻结在阶段结论中的版本不受影响。"""
        material = self._material(material_id)
        case = self._case(material.case_id)
        if case.stage is CaseStage.CLOSED:
            raise InvalidStateError("案件已结案，材料归档不可更新")
        if material.invalidated:
            raise InvalidStateError("证据已失效，不能登记新版本")
        version = MaterialVersion(
            version=len(material.versions) + 1,
            digest=content_digest(content),
            registered_by=actor,
            at=at or _utcnow(),
        )
        material.versions.append(version)
        self._log_decision(
            material.case_id,
            DecisionKind.VERSION,
            actor,
            {
                "material_id": material_id,
                "version": version.version,
                "digest": version.digest,
            },
            version.at,
        )
        return version

    # -- 调查假设与对象关系 --------------------------------------------------

    def register_relation(
        self,
        case_id: str,
        actor: str,
        hypothesis: str,
        subject: str,
        predicate: str,
        obj: str,
        at: Optional[datetime] = None,
    ) -> Relation:
        """在调查假设下登记一条对象关系。"""
        case = self._case(case_id)
        relation = Relation(
            hypothesis=hypothesis,
            subject=subject,
            predicate=predicate,
            obj=obj,
            actor=actor,
            at=at or _utcnow(),
        )
        case.relations.append(relation)
        self._log_decision(
            case_id,
            DecisionKind.RELATION,
            actor,
            {
                "hypothesis": hypothesis,
                "subject": subject,
                "predicate": predicate,
                "object": obj,
            },
            relation.at,
        )
        return relation

    # -- 候选对应与复核 ------------------------------------------------------

    def propose_correspondence(
        self,
        case_id: str,
        actor: str,
        left: str,
        right: str,
        note: str = "",
        at: Optional[datetime] = None,
    ) -> str:
        """建立候选对应（如视频片段与结算记录），不自动合并身份。"""
        self._case(case_id)
        for mid in (left, right):
            material = self._material(mid)
            if material.case_id != case_id:
                raise NotFoundError(f"材料不属于案件 {case_id}：{mid}")
        if left == right:
            raise InvalidStateError("不能与自身建立对应")
        link_id = self._next_id(case_id, "L")
        link = Correspondence(
            link_id=link_id,
            case_id=case_id,
            left=left,
            right=right,
            note=note,
            proposed_by=actor,
            at=at or _utcnow(),
        )
        self._links[link_id] = link
        self._log_decision(
            case_id,
            DecisionKind.PROPOSAL,
            actor,
            {"link_id": link_id, "left": left, "right": right, "note": note},
            link.at,
        )
        return link_id

    def review_correspondence(
        self,
        link_id: str,
        reviewer: str,
        approve: bool,
        note: str = "",
        at: Optional[datetime] = None,
    ) -> Correspondence:
        """复核候选对应；须由提案人以外的人员复核，复核后才可合并身份。"""
        link = self._links.get(link_id)
        if link is None:
            raise NotFoundError(f"候选对应不存在：{link_id}")
        if link.status is not LinkStatus.CANDIDATE:
            raise ReviewError("该对应已复核，不可重复复核")
        if reviewer == link.proposed_by:
            raise ReviewError("提案人不得自行复核")
        link.status = LinkStatus.CONFIRMED if approve else LinkStatus.REJECTED
        link.reviewed_by = reviewer
        link.review_note = note
        self._log_decision(
            link.case_id,
            DecisionKind.REVIEW,
            reviewer,
            {
                "link_id": link_id,
                "approved": approve,
                "note": note,
            },
            at or _utcnow(),
        )
        return link

    def identity_links(self, case_id: str) -> tuple:
        """已确认的身份关联；仅复核通过的对应才进入身份合并视图。"""
        self._case(case_id)
        return tuple(
            link
            for link in self._links.values()
            if link.case_id == case_id and link.status is LinkStatus.CONFIRMED
        )

    # -- 阶段流转 ------------------------------------------------------------

    def escalate(
        self,
        case_id: str,
        actor: str,
        to_stage: CaseStage,
        rationale: str,
        at: Optional[datetime] = None,
    ) -> CaseStage:
        """线索升级：只能由负责人推进，且只能向前。"""
        case = self._case(case_id)
        if actor != case.lead:
            raise AuthorizationError("只有负责人可以推进案件阶段")
        current = STAGE_ORDER.index(case.stage)
        target = STAGE_ORDER.index(to_stage)
        if target <= current:
            raise StageError("案件阶段只能向前推进")
        case.stage = to_stage
        self._log_decision(
            case_id,
            DecisionKind.ESCALATION,
            actor,
            {"from": STAGE_ORDER[current].value, "to": to_stage.value,
             "rationale": rationale},
            at or _utcnow(),
        )
        return case.stage

    # -- 证据失效、借阅与并行分析 --------------------------------------------

    def invalidate(
        self,
        material_id: str,
        actor: str,
        reason: str,
        at: Optional[datetime] = None,
    ) -> Material:
        """宣告证据失效：留痕，之后不得借阅、更新或用于新结论。"""
        material = self._material(material_id)
        if material.invalidated:
            raise InvalidStateError("证据已处于失效状态")
        material.invalidated = True
        material.invalidation_reason = reason
        self._log_decision(
            material.case_id,
            DecisionKind.INVALIDATION,
            actor,
            {"material_id": material_id, "reason": reason},
            at or _utcnow(),
        )
        return material

    def borrow(
        self,
        material_id: str,
        actor: str,
        department: str,
        purpose: str,
        at: Optional[datetime] = None,
    ) -> Transfer:
        """跨部门借阅：材料的可使用范围须允许，移交记录追加到保管链。"""
        material = self._material(material_id)
        if material.invalidated:
            raise InvalidStateError("证据已失效，不能借阅")
        if "cross-dept" not in material.scope:
            raise AuthorizationError("材料的可使用范围不含跨部门借阅")
        transfer = Transfer(
            department=department,
            actor=actor,
            purpose=purpose,
            at=at or _utcnow(),
        )
        material.custody.append(transfer)
        self._log_decision(
            material.case_id,
            DecisionKind.BORROW,
            actor,
            {
                "material_id": material_id,
                "department": department,
                "purpose": purpose,
            },
            transfer.at,
        )
        return transfer

    def record_analysis(
        self,
        case_id: str,
        actor: str,
        note: str,
        material_ids: Iterable[str] = (),
        at: Optional[datetime] = None,
    ) -> Decision:
        """并行分析留痕：各小组的分析结论写入日志，互不覆盖。"""
        self._case(case_id)
        for mid in material_ids:
            self._material(mid)
        return self._log_decision(
            case_id,
            DecisionKind.ANALYSIS,
            actor,
            {"note": note, "material_ids": list(material_ids)},
            at or _utcnow(),
        )

    # -- 阶段结论 ------------------------------------------------------------

    def submit_conclusion(
        self,
        case_id: str,
        actor: str,
        findings: Iterable[Finding],
        at: Optional[datetime] = None,
    ) -> str:
        """负责人提交阶段结论，冻结所用材料的当前版本。"""
        case = self._case(case_id)
        if actor != case.lead:
            raise AuthorizationError("只有负责人可以提交阶段结论")
        findings = tuple(findings)
        if not findings:
            raise InvalidStateError("阶段结论至少包含一条事实认定")
        frozen: dict = {}
        for finding in findings:
            if not finding.material_ids:
                raise InvalidStateError("事实认定必须注明依据材料")
            for mid in finding.material_ids:
                material = self._material(mid)
                if material.case_id != case_id:
                    raise NotFoundError(f"材料不属于案件 {case_id}：{mid}")
                if material.invalidated:
                    raise InvalidStateError(f"证据已失效，不能用于结论：{mid}")
                frozen[mid] = material.current
        conclusion_id = self._next_id(case_id, "C")
        conclusion = StageConclusion(
            conclusion_id=conclusion_id,
            case_id=case_id,
            stage=case.stage,
            submitted_by=actor,
            at=at or _utcnow(),
            findings=findings,
            frozen=frozen,
        )
        self._conclusions[conclusion_id] = conclusion
        self._log_decision(
            case_id,
            DecisionKind.CONCLUSION,
            actor,
            {
                "conclusion_id": conclusion_id,
                "stage": case.stage.value,
                "frozen": {
                    mid: {"version": v.version, "digest": v.digest}
                    for mid, v in sorted(frozen.items())
                },
            },
            conclusion.at,
        )
        return conclusion_id

    def conclusion_report(self, conclusion_id: str) -> dict:
        """阶段结论报告：已印证事实、待核关联、版本状态与材料移交记录。"""
        conclusion = self._conclusions.get(conclusion_id)
        if conclusion is None:
            raise NotFoundError(f"阶段结论不存在：{conclusion_id}")
        frozen = conclusion.frozen

        corroborated, pending_facts = [], []
        for finding in conclusion.findings:
            usable = [
                self._materials[mid]
                for mid in finding.material_ids
                if not self._materials[mid].invalidated
            ]
            sources = {m.source for m in usable}
            entry = {
                "fact": finding.fact,
                "material_ids": list(finding.material_ids),
                "sources": sorted(sources),
            }
            # 至少两个相互独立的来源支持，事实才视为相互印证。
            (corroborated if len(sources) >= 2 else pending_facts).append(entry)

        frozen_ids = set(frozen)
        confirmed_links, pending_links = [], []
        for link in self._links.values():
            if link.case_id != conclusion.case_id:
                continue
            if not {link.left, link.right}.issubset(frozen_ids):
                continue
            item = {
                "link_id": link.link_id,
                "left": link.left,
                "right": link.right,
                "reviewed_by": link.reviewed_by,
            }
            if link.status is LinkStatus.CONFIRMED:
                confirmed_links.append(item)
            elif link.status is LinkStatus.CANDIDATE:
                pending_links.append(item)

        version_status = {}
        for mid, frozen_version in frozen.items():
            material = self._materials[mid]
            status = (
                "unchanged"
                if material.current.digest == frozen_version.digest
                else "updated"
            )
            if material.invalidated:
                status = "invalidated"
            version_status[mid] = {
                "frozen_version": frozen_version.version,
                "frozen_digest": frozen_version.digest,
                "status": status,
            }

        custody = {
            mid: list(self._materials[mid].custody) for mid in sorted(frozen)
        }

        return {
            "conclusion_id": conclusion_id,
            "case_id": conclusion.case_id,
            "stage": conclusion.stage,
            "corroborated_facts": corroborated,
            "pending_facts": pending_facts,
            "confirmed_links": confirmed_links,
            "pending_links": pending_links,
            "version_status": version_status,
            "custody": custody,
        }

    # -- 可见性与日志 ---------------------------------------------------------

    def visible_materials(self, case_id: str, group: str) -> tuple:
        """按小组与当前阶段返回履职所需可见的材料；负责人始终全量可见。"""
        case = self._case(case_id)
        materials = [
            m for m in self._materials.values() if m.case_id == case_id
        ]
        if group != case.lead:
            materials = [m for m in materials if not m.invalidated]
            allowed = self._access_policy.get(group, {}).get(case.stage)
            if allowed is None:
                if group not in self._access_policy:
                    materials = []
            else:
                materials = [m for m in materials if m.kind in allowed]
        return tuple(sorted(materials, key=lambda m: m.material_id))

    def decision_log(self, case_id: Optional[str] = None) -> tuple:
        """决定日志（只读视图）；条目为不可变对象，不可覆盖。"""
        if case_id is None:
            return tuple(self._log)
        return tuple(e for e in self._log if e.case_id == case_id)

    def verify_log(self) -> bool:
        """校验决定日志的哈希链完整、未被覆盖或插入。"""
        prev_hash = "GENESIS"
        for seq, entry in enumerate(self._log):
            if entry.seq != seq or entry.prev_hash != prev_hash:
                return False
            payload = json.dumps(
                {
                    "seq": entry.seq,
                    "case_id": entry.case_id,
                    "kind": entry.kind.value,
                    "actor": entry.actor,
                    "detail": entry.detail,
                    "at": entry.at.isoformat(),
                    "prev_hash": entry.prev_hash,
                },
                ensure_ascii=False,
                sort_keys=True,
            )
            if entry.hash != _sha256_text(payload):
                return False
            prev_hash = entry.hash
        return True
