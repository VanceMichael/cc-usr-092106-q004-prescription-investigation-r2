"""证据室：在只增不改的日志之上执行办案规则并重建当前状态。"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from .log import append_event, verify_chain
from .types import (
    AccessScope,
    CaseStage,
    DecisionKind,
    LinkStatus,
    STAGE_ORDER,
    Team,
)


class RuleViolation(Exception):
    """操作不符合证据室规则（如无授权取证、单条疑点升级）。"""


def _parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts)


class EvidenceRoom:
    """从事件日志重建的证据室状态。

    外部代码只调用以动词命名的业务方法；所有状态变化都落到
    ``self.events`` 中，重新加载日志即可完整重建。
    """

    # ---- 载入与重建 ----------------------------------------------------

    def __init__(self, events: list[dict] | None = None):
        self.events: list[dict] = events or []
        verify_chain(self.events)
        self._rebuild()

    def _rebuild(self) -> None:
        self.case: dict | None = None
        self.stage: CaseStage = CaseStage.INITIAL
        self.subjects: dict[str, dict] = {}
        self.hypotheses: dict[str, dict] = {}
        self.authorizations: dict[str, dict] = {}
        self.materials: dict[str, dict] = {}
        self.links: dict[str, dict] = {}
        self.loans: dict[str, dict] = {}
        self.analyses: list[dict] = []
        self.conclusions: list[dict] = []
        self.custody: dict[str, list[dict]] = defaultdict(list)
        self.sealed_material_ids: set[str] = set()
        for event in self.events:
            self._apply(event)

    def _apply(self, event: dict) -> None:
        kind = event["kind"]
        p = event["payload"]
        ts = event["timestamp"]

        if kind == DecisionKind.CASE_OPENED:
            self.case = {"case_id": p["case_id"], "title": p["title"],
                         "opened_by": event["actor"], "opened_at": ts}

        elif kind == DecisionKind.SUBJECT_REGISTERED:
            self.subjects[p["subject_id"]] = {
                "subject_id": p["subject_id"],
                "label": p["label"],
                "role": p["role"],
                "note": p.get("note", ""),
            }

        elif kind == DecisionKind.HYPOTHESIS_REGISTERED:
            self.hypotheses[p["hypothesis_id"]] = {
                "hypothesis_id": p["hypothesis_id"],
                "statement": p["statement"],
                "subject_ids": list(p["subject_ids"]),
                "basis": p.get("basis", ""),
                "status": "pending",
                "materials": [],
                "escalated_at": None,
            }

        elif kind == DecisionKind.LEAD_ESCALATED:
            h = self.hypotheses[p["hypothesis_id"]]
            h["status"] = "escalated"
            h["escalated_at"] = ts
            h["escalate_reason"] = p["reason"]

        elif kind == DecisionKind.AUTHORIZATION_GRANTED:
            self.authorizations[p["authorization_id"]] = {
                **p,
                "granted_at": ts,
            }

        elif kind == DecisionKind.MATERIAL_REGISTERED:
            auth = self.authorizations[p["authorization_id"]]
            material = {
                "material_id": p["material_id"],
                "category": p["category"],
                "title": p["title"],
                "source": p["source"],
                "obtained_at": p["obtained_at"],
                "checksum": p["checksum"],
                "authorization_id": p["authorization_id"],
                "collected_by": p["collected_by"],
                "hypothesis_ids": list(p.get("hypothesis_ids", [])),
                "allowed_teams": list(auth["teams"]),
                "min_stage": auth["min_stage"],
                "valid": True,
                "invalidated_at": None,
                "invalidate_reason": None,
                "custodian": p["custodian"],
                "registered_at": ts,
            }
            self.materials[p["material_id"]] = material
            self.custody[p["material_id"]].append(
                {"at": ts, "custodian": p["custodian"],
                 "actor": event["actor"], "reason": "登记入室"})
            for hid in material["hypothesis_ids"]:
                self.hypotheses[hid]["materials"].append(p["material_id"])

        elif kind == DecisionKind.CANDIDATE_LINK_PROPOSED:
            self.links[p["link_id"]] = {
                **p,
                "status": LinkStatus.CANDIDATE,
                "proposed_by": event["actor"],
                "proposed_at": ts,
                "reviewed_by": None,
                "reviewed_at": None,
                "review_reason": None,
            }

        elif kind == DecisionKind.IDENTITY_REVIEWED:
            link = self.links[p["link_id"]]
            link["status"] = p["verdict"]
            link["reviewed_by"] = event["actor"]
            link["reviewed_at"] = ts
            link["review_reason"] = p["reason"]

        elif kind == DecisionKind.MATERIAL_INVALIDATED:
            m = self.materials[p["material_id"]]
            m["valid"] = False
            m["invalidated_at"] = ts
            m["invalidate_reason"] = p["reason"]

        elif kind == DecisionKind.ACCESS_GRANTED:
            self.loans[p["loan_id"]] = {**p, "granted_at": ts,
                                        "granted_by": event["actor"]}

        elif kind == DecisionKind.PARALLEL_ANALYSIS:
            self.analyses.append({**p, "recorded_at": ts,
                                  "recorded_by": event["actor"]})

        elif kind == DecisionKind.STAGE_CONCLUSION_SUBMITTED:
            self.conclusions.append({**p, "submitted_at": ts,
                                     "submitted_by": event["actor"]})
            for mid in p["frozen_materials"]:
                self.sealed_material_ids.add(mid if isinstance(mid, str)
                                             else mid["material_id"])
            order = STAGE_ORDER
            idx = order[CaseStage(p["stage"])]
            nxt = idx + 1
            if nxt < len(STAGE_ORDER):
                self.stage = list(CaseStage)[nxt]

        elif kind == DecisionKind.MATERIAL_TRANSFERRED:
            m = self.materials[p["material_id"]]
            m["custodian"] = p["to_custodian"]
            self.custody[p["material_id"]].append(
                {"at": ts, "custodian": p["to_custodian"],
                 "actor": event["actor"], "reason": p["reason"]})

    def _add(self, kind: DecisionKind, payload: dict, actor: str,
             timestamp: str | None = None) -> dict:
        event = append_event(self.events, kind, payload, actor, timestamp)
        self._apply(event)
        return event

    # ---- 小工具 --------------------------------------------------------

    def _require_material(self, mid: str) -> dict:
        m = self.materials.get(mid)
        if m is None:
            raise RuleViolation(f"材料不存在：{mid}")
        return m

    def _valid_materials(self, hypothesis_id: str) -> list[dict]:
        h = self.hypotheses.get(hypothesis_id)
        if h is None:
            raise RuleViolation(f"调查假设不存在：{hypothesis_id}")
        return [self.materials[mid] for mid in h["materials"]
                if self.materials[mid]["valid"]]

    def _active_authorization(self, auth_id: str, category: str,
                              team: str, at: datetime) -> dict:
        auth = self.authorizations.get(auth_id)
        if auth is None:
            raise RuleViolation(f"取证授权不存在：{auth_id}")
        if category not in auth["categories"]:
            raise RuleViolation(
                f"授权 {auth_id} 不覆盖材料类别 {category}")
        if team not in auth["teams"]:
            raise RuleViolation(f"授权 {auth_id} 不含 {team} 小组")
        if not (_parse(auth["valid_from"]) <= at <= _parse(auth["valid_to"])):
            raise RuleViolation(f"授权 {auth_id} 不在有效期限内")
        if STAGE_ORDER[CaseStage(auth["min_stage"])] > STAGE_ORDER[self.stage]:
            raise RuleViolation(
                f"授权 {auth_id} 要求案件到达 {auth['min_stage']} 阶段")
        return auth

    # ---- 立案与对象 ----------------------------------------------------

    def open_case(self, case_id: str, title: str, actor: str,
                  timestamp: str | None = None) -> dict:
        if self.case is not None:
            raise RuleViolation("案件已立案，不能重复开立")
        return self._add(DecisionKind.CASE_OPENED,
                         {"case_id": case_id, "title": title}, actor, timestamp)

    def register_subject(self, subject_id: str, label: str, role: str,
                         actor: str, note: str = "",
                         timestamp: str | None = None) -> dict:
        if subject_id in self.subjects:
            raise RuleViolation(f"对象已登记：{subject_id}")
        return self._add(DecisionKind.SUBJECT_REGISTERED,
                         {"subject_id": subject_id, "label": label,
                          "role": role, "note": note}, actor, timestamp)

    def register_hypothesis(self, hypothesis_id: str, statement: str,
                            subject_ids: list[str], actor: str,
                            basis: str = "",
                            timestamp: str | None = None) -> dict:
        """按调查假设登记对象关系。假设成立之初只是待查线索。"""
        if hypothesis_id in self.hypotheses:
            raise RuleViolation(f"调查假设已登记：{hypothesis_id}")
        for sid in subject_ids:
            if sid not in self.subjects:
                raise RuleViolation(f"对象尚未登记：{sid}")
        return self._add(DecisionKind.HYPOTHESIS_REGISTERED,
                         {"hypothesis_id": hypothesis_id,
                          "statement": statement,
                          "subject_ids": subject_ids,
                          "basis": basis}, actor, timestamp)

    # ---- 线索升级 ------------------------------------------------------

    def escalate_lead(self, hypothesis_id: str, actor: str, reason: str,
                      timestamp: str | None = None) -> dict:
        """线索升级为正式调查方向。

        不能只凭一条同日开药记录：升级时必须已有至少两类、且相互
        存在已复核联结的有效材料支撑，避免错查正常患者、过早接触对象。
        """
        h = self.hypotheses.get(hypothesis_id)
        if h is None:
            raise RuleViolation(f"调查假设不存在：{hypothesis_id}")
        if h["status"] == "escalated":
            raise RuleViolation("线索已升级，决定保留在日志中")
        materials = self._valid_materials(hypothesis_id)
        categories = {m["category"] for m in materials}
        if len(categories) < 2:
            raise RuleViolation(
                "材料类别单一（仅一条结算/开药记录不足以升级线索），"
                "应先补充其他来源材料，避免打草惊蛇")
        if not self._has_cross_category_corroboration(
                {m["material_id"] for m in materials}):
            raise RuleViolation(
                "多类材料之间尚无经复核的联结，不能据此升级线索")
        return self._add(DecisionKind.LEAD_ESCALATED,
                         {"hypothesis_id": hypothesis_id, "reason": reason},
                         actor, timestamp)

    # ---- 授权与材料 ----------------------------------------------------

    def grant_authorization(self, authorization_id: str, categories: list[str],
                            teams: list[str], valid_from: str, valid_to: str,
                            actor: str, basis: str,
                            min_stage: str = CaseStage.INITIAL,
                            supplemental_of: str | None = None,
                            timestamp: str | None = None) -> dict:
        """登记取证授权；补充授权须指向一份既有授权。"""
        if authorization_id in self.authorizations:
            raise RuleViolation(f"授权已登记：{authorization_id}")
        if supplemental_of is not None and supplemental_of not in self.authorizations:
            raise RuleViolation(f"被补充的授权不存在：{supplemental_of}")
        if _parse(valid_to) <= _parse(valid_from):
            raise RuleViolation("授权截止时间必须晚于起始时间")
        return self._add(DecisionKind.AUTHORIZATION_GRANTED,
                         {"authorization_id": authorization_id,
                          "categories": list(categories),
                          "teams": list(teams),
                          "valid_from": valid_from,
                          "valid_to": valid_to,
                          "min_stage": min_stage,
                          "basis": basis,
                          "supplemental_of": supplemental_of},
                         actor, timestamp)

    def register_material(self, material_id: str, category: str, title: str,
                          source: str, obtained_at: str, checksum: str,
                          authorization_id: str, collected_by: str,
                          custodian: str, actor: str,
                          hypothesis_ids: list[str] | None = None,
                          timestamp: str | None = None) -> dict:
        """登记一份材料：来源、取得时间、校验摘要与授权缺一不可。"""
        if material_id in self.materials:
            raise RuleViolation(f"材料已登记：{material_id}")
        if not checksum.startswith("sha256:") or len(checksum) != 71:
            raise RuleViolation("校验摘要须为 sha256: 加 64 位十六进制")
        for hid in hypothesis_ids or []:
            if hid not in self.hypotheses:
                raise RuleViolation(f"关联的调查假设不存在：{hid}")
        self._active_authorization(
            authorization_id, category, collected_by, _parse(obtained_at))
        return self._add(DecisionKind.MATERIAL_REGISTERED,
                         {"material_id": material_id, "category": category,
                          "title": title, "source": source,
                          "obtained_at": obtained_at, "checksum": checksum,
                          "authorization_id": authorization_id,
                          "collected_by": collected_by,
                          "custodian": custodian,
                          "hypothesis_ids": list(hypothesis_ids or [])},
                         actor, timestamp)

    # ---- 候选对应与身份复核 --------------------------------------------

    def propose_candidate_link(self, link_id: str, material_a: str,
                               material_b: str, link_kind: str, basis: str,
                               actor: str, confidence: float | None = None,
                               timestamp: str | None = None) -> dict:
        """在两份材料之间建立候选对应（如视频片段 ↔ 结算记录）。

        候选只表示"可能对应"：不合并身份、不作为既成事实。
        """
        if link_id in self.links:
            raise RuleViolation(f"候选对应已登记：{link_id}")
        for mid in (material_a, material_b):
            m = self._require_material(mid)
            if not m["valid"]:
                raise RuleViolation(f"材料已失效，不能建立对应：{mid}")
        if material_a == material_b:
            raise RuleViolation("不能在同一份材料内部建立对应")
        if link_kind not in ("identity", "event"):
            raise RuleViolation("联结类型须为 identity（身份）或 event（事件）")
        if confidence is not None and not 0.0 <= confidence <= 1.0:
            raise RuleViolation("置信度须在 0 到 1 之间")
        return self._add(DecisionKind.CANDIDATE_LINK_PROPOSED,
                         {"link_id": link_id, "material_a": material_a,
                          "material_b": material_b, "link_kind": link_kind,
                          "basis": basis, "confidence": confidence},
                         actor, timestamp)

    def review_identity(self, link_id: str, verdict: str, actor: str,
                        reason: str,
                        timestamp: str | None = None) -> dict:
        """对候选对应进行人工复核：确认或排除，决定不可覆盖。"""
        link = self.links.get(link_id)
        if link is None:
            raise RuleViolation(f"候选对应不存在：{link_id}")
        if link["status"] != LinkStatus.CANDIDATE:
            raise RuleViolation("该对应已经复核，结论不得覆盖；如需改变请另作决定")
        if verdict not in (LinkStatus.CONFIRMED, LinkStatus.REJECTED):
            raise RuleViolation("复核结论须为 confirmed 或 rejected")
        if not reason:
            raise RuleViolation("复核须说明理由")
        return self._add(DecisionKind.IDENTITY_REVIEWED,
                         {"link_id": link_id, "verdict": verdict,
                          "reason": reason}, actor, timestamp)

    def identity_groups(self) -> list[list[str]]:
        """经复核确认的身份合并组。未经复核的候选不并入。"""
        parent: dict[str, str] = {}

        def find(x: str) -> str:
            parent.setdefault(x, x)
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        for link in self.links.values():
            if (link["link_kind"] == "identity"
                    and link["status"] == LinkStatus.CONFIRMED):
                a, b = link["material_a"], link["material_b"]
                parent[find(a)] = find(b)
        groups: dict[str, list[str]] = defaultdict(list)
        for mid in self.materials:
            groups[find(mid)].append(mid)
        return [sorted(members) for members in groups.values()
                if len(members) > 1]

    def _confirmed_edges(self, node_set: set[str]) -> dict[str, set[str]]:
        adj: dict[str, set[str]] = defaultdict(set)
        for link in self.links.values():
            if link["status"] != LinkStatus.CONFIRMED:
                continue
            a, b = link["material_a"], link["material_b"]
            if a in node_set and b in node_set:
                ma, mb = self.materials.get(a), self.materials.get(b)
                if ma and mb and ma["valid"] and mb["valid"]:
                    adj[a].add(b)
                    adj[b].add(a)
        return adj

    def _corroborated_components(self, node_set: set[str]) -> list[set[str]]:
        """返回由已复核联结构成的连通分量（仅限有效材料）。"""
        adj = self._confirmed_edges(node_set)
        remaining = set(node_set)
        components: list[set[str]] = []
        while remaining:
            start = remaining.pop()
            comp = {start}
            queue = [start]
            while queue:
                cur = queue.pop()
                for nxt in adj.get(cur, ()):  # noqa: PERF401 - 可读性优先
                    if nxt not in comp:
                        comp.add(nxt)
                        remaining.discard(nxt)
                        queue.append(nxt)
            components.append(comp)
        return components

    def _has_cross_category_corroboration(self, node_set: set[str]) -> bool:
        """是否存在一条跨至少两个材料类别的已复核联结链。"""
        for comp in self._corroborated_components(node_set):
            cats = {self.materials[mid]["category"] for mid in comp}
            if len(cats) >= 2:
                return True
        return False

    def _connected_through_confirmed(self, node_set: set[str]) -> bool:
        components = self._corroborated_components(node_set)
        return len(components) == 1 and len(node_set) >= 2

    # ---- 失效、借阅、并行分析 ------------------------------------------

    def invalidate_material(self, material_id: str, reason: str, actor: str,
                            timestamp: str | None = None) -> dict:
        """宣告材料失效。原登记事件保留，后续投影不再把它算作有效材料。"""
        m = self._require_material(material_id)
        if not m["valid"]:
            raise RuleViolation("材料已处于失效状态")
        if not reason:
            raise RuleViolation("失效须说明理由")
        return self._add(DecisionKind.MATERIAL_INVALIDATED,
                         {"material_id": material_id, "reason": reason},
                         actor, timestamp)

    def grant_loan(self, loan_id: str, material_id: str, borrower_team: str,
                   purpose: str, valid_from: str, valid_to: str, actor: str,
                   timestamp: str | None = None) -> dict:
        """跨部门借阅：限定用途与时间窗；已随结论冻结的材料停止外借。"""
        m = self._require_material(material_id)
        if loan_id in self.loans:
            raise RuleViolation(f"借阅记录已存在：{loan_id}")
        if not m["valid"]:
            raise RuleViolation("失效材料不得外借")
        if material_id in self.sealed_material_ids:
            raise RuleViolation("材料已随阶段结论冻结，停止外借")
        if not purpose:
            raise RuleViolation("借阅须说明用途")
        if _parse(valid_to) <= _parse(valid_from):
            raise RuleViolation("借阅截止时间必须晚于起始时间")
        return self._add(DecisionKind.ACCESS_GRANTED,
                         {"loan_id": loan_id, "material_id": material_id,
                          "borrower_team": borrower_team, "purpose": purpose,
                          "valid_from": valid_from, "valid_to": valid_to},
                         actor, timestamp)

    def record_parallel_analysis(self, analysis_id: str, team: str,
                                 material_ids: list[str], summary: str,
                                 actor: str,
                                 confirms: list[str] | None = None,
                                 timestamp: str | None = None) -> dict:
        """小组并行分析留痕。所用材料必须是该组履职可见范围之内的。"""
        if any(a["analysis_id"] == analysis_id for a in self.analyses):
            raise RuleViolation(f"分析记录已存在：{analysis_id}")
        at = _parse(timestamp) if timestamp else _parse(self.events[-1]["timestamp"])
        visible = {m["material_id"] for m in self.view_for(team, at)["materials"]}
        for mid in material_ids:
            self._require_material(mid)
            if mid not in visible:
                raise RuleViolation(
                    f"{team} 小组对材料 {mid} 没有履职所需的可见权限")
        for lid in confirms or []:
            if lid not in self.links:
                raise RuleViolation(f"被印证的联结不存在：{lid}")
        return self._add(DecisionKind.PARALLEL_ANALYSIS,
                         {"analysis_id": analysis_id, "team": team,
                          "material_ids": list(material_ids),
                          "summary": summary,
                          "confirms": list(confirms or [])},
                         actor, timestamp)

    # ---- 阶段结论与冻结 ------------------------------------------------

    def submit_stage_conclusion(self, stage: str, facts: list[dict],
                                pending: list[dict], actor: str,
                                timestamp: str | None = None) -> dict:
        """负责人提交阶段结论，冻结所用材料版本。

        - facts：已相互印证的事实，每份事实引用的材料须通过已复核联结
          连成一体，且覆盖至少两个类别；
        - pending：仍只是待核的关联，必须引用未经复核（候选）的联结，
          不得写成既成事实。
        """
        if self.case is None:
            raise RuleViolation("案件尚未开立")
        if CaseStage(stage) != self.stage:
            raise RuleViolation(
                f"当前处于 {self.stage} 阶段，只能提交该阶段结论")
        frozen: list[dict] = []
        seen: set[str] = set()
        for fact in facts:
            refs = set(fact.get("material_refs", []))
            if len(refs) < 2:
                raise RuleViolation(
                    f"事实“{fact.get('statement', '')}”材料不足两份，不能认定")
            for mid in refs:
                m = self._require_material(mid)
                if not m["valid"]:
                    raise RuleViolation(
                        f"事实引用了失效材料：{mid}")
            cats = {self.materials[mid]["category"] for mid in refs}
            if len(cats) < 2:
                raise RuleViolation(
                    f"事实“{fact.get('statement', '')}”仅由单一类别材料支撑，"
                    "谈不上相互印证")
            if not self._connected_through_confirmed(refs):
                raise RuleViolation(
                    f"事实“{fact.get('statement', '')}”所引材料之间"
                    "缺少已复核联结，不能写成已印证事实")
            for lid in fact.get("link_refs", []):
                link = self.links.get(lid)
                if link is None:
                    raise RuleViolation(f"事实引用的联结不存在：{lid}")
                if link["status"] != LinkStatus.CONFIRMED:
                    raise RuleViolation(
                        f"事实引用的联结 {lid} 未经复核确认")
            for mid in refs:
                seen.add(mid)
        for item in pending:
            for lid in item.get("link_refs", []):
                link = self.links.get(lid)
                if link is None:
                    raise RuleViolation(f"待核关联引用的联结不存在：{lid}")
                if link["status"] != LinkStatus.CANDIDATE:
                    raise RuleViolation(
                        f"联结 {lid} 已复核，应作为事实或排除项，不能列为待核")
                for mid in (link["material_a"], link["material_b"]):
                    m = self._require_material(mid)
                    if not m["valid"]:
                        raise RuleViolation(
                            f"待核关联引用了失效材料：{mid}")
                    seen.add(mid)
        # 冻结清单按材料号排序，保证同一案件重放结果逐字节一致
        frozen = sorted(
            ({"material_id": mid,
              "checksum": self.materials[mid]["checksum"],
              "category": self.materials[mid]["category"],
              "custodian_at_freeze": self.materials[mid]["custodian"]}
             for mid in seen),
            key=lambda entry: entry["material_id"])
        frozen_event_seq = len(self.events)
        freeze_hash = self.events[-1]["hash"] if self.events else "GENESIS"
        return self._add(
            DecisionKind.STAGE_CONCLUSION_SUBMITTED,
            {"stage": stage,
             "facts": [{"fact_id": f["fact_id"],
                        "statement": f["statement"],
                        "material_refs": sorted(f["material_refs"]),
                        "link_refs": sorted(f.get("link_refs", []))}
                       for f in facts],
             "pending": [{"item_id": p["item_id"],
                          "statement": p["statement"],
                          "link_refs": list(p["link_refs"])}
                         for p in pending],
             "frozen_materials": frozen,
             "frozen_event_seq": frozen_event_seq,
             "freeze_hash": freeze_hash},
            actor, timestamp)

    def transfer_material(self, material_id: str, to_custodian: str,
                          actor: str, reason: str,
                          timestamp: str | None = None) -> dict:
        """移交材料保管责任。材料已冻结也可移交，轨迹完整保留。"""
        m = self._require_material(material_id)
        if not reason:
            raise RuleViolation("移交须说明理由")
        if m["custodian"] == to_custodian:
            raise RuleViolation("接收保管人与当前保管人相同")
        return self._add(DecisionKind.MATERIAL_TRANSFERRED,
                         {"material_id": material_id,
                          "to_custodian": to_custodian, "reason": reason},
                         actor, timestamp)

    # ---- 可见范围（最小必要原则）---------------------------------------

    def _active_loans(self, material_id: str, team: str,
                      at: datetime) -> list[dict]:
        out = []
        for loan in self.loans.values():
            if (loan["material_id"] == material_id
                    and loan["borrower_team"] == team
                    and _parse(loan["valid_from"]) <= at <= _parse(loan["valid_to"])):
                out.append(loan)
        return out

    def view_for(self, team: str, at: datetime | None = None) -> dict:
        """返回某小组在指定时点可见的材料与联结。

        - 未到授权要求阶段的材料不可见；
        - 只对授权小组开放，或在有效借阅窗内向借入方开放；
        - 候选联结要求两端材料对该组都可见，避免跨组泄露待核身份判断。
        """
        at = at or datetime.now(timezone.utc)
        visible: dict[str, dict] = {}
        for mid, m in self.materials.items():
            stage_ok = STAGE_ORDER[CaseStage(m["min_stage"])] <= STAGE_ORDER[self.stage]
            direct = team in m["allowed_teams"]
            loaned = bool(self._active_loans(mid, team, at))
            if stage_ok and (direct or loaned):
                visible[mid] = m
        visible_links = []
        for link in self.links.values():
            if link["material_a"] in visible and link["material_b"] in visible:
                visible_links.append(link)
        visible_hypotheses = []
        for h in self.hypotheses.values():
            if team == Team.REVIEW or any(
                    mid in visible for mid in h["materials"]):
                visible_hypotheses.append({
                    "hypothesis_id": h["hypothesis_id"],
                    "statement": h["statement"],
                    "status": h["status"],
                })
        return {"team": team, "stage": self.stage,
                "materials": list(visible.values()),
                "links": visible_links,
                "hypotheses": visible_hypotheses}

    # ---- 结论报告与移交轨迹 --------------------------------------------

    def custody_trail(self, material_id: str) -> list[dict]:
        self._require_material(material_id)
        return list(self.custody[material_id])

    def conclusion_report(self, index: int = -1) -> dict:
        """生成阶段结论说明：印证事实、待核关联、冻结版本与事后移交。"""
        if not self.conclusions:
            raise RuleViolation("尚无阶段结论")
        c = self.conclusions[index]
        frozen = []
        for entry in c["frozen_materials"]:
            mid = entry["material_id"]
            m = self.materials[mid]
            trail = self.custody_trail(mid)
            frozen.append({
                **entry,
                "checksum_now": m["checksum"],
                "version_changed": m["checksum"] != entry["checksum"],
                "valid_now": m["valid"],
                "invalidate_reason": m["invalidate_reason"],
                "custodian_now": m["custodian"],
                "transferred_after_freeze": [
                    t for t in trail if t["at"] > c["submitted_at"]],
            })
        return {"case_id": self.case["case_id"], "stage": c["stage"],
                "submitted_by": c["submitted_by"],
                "submitted_at": c["submitted_at"],
                "freeze_hash": c["freeze_hash"],
                "frozen_event_seq": c["frozen_event_seq"],
                "corroborated_facts": c["facts"],
                "pending_associations": c["pending"],
                "frozen_materials": frozen}

    # ---- 序列化 --------------------------------------------------------

    def to_dict(self) -> dict:
        return {"domain": "prescription-investigation",
                "log_version": 1,
                "case_id": self.case["case_id"] if self.case else None,
                "events": self.events}

    @classmethod
    def from_dict(cls, data: dict) -> "EvidenceRoom":
        if data.get("domain") != "prescription-investigation":
            raise ValueError("领域标识不匹配")
        if data.get("log_version") != 1:
            raise ValueError("不支持的日志版本")
        return cls(data.get("events", []))
