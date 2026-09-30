import copy
import json
import unittest
from datetime import datetime
from pathlib import Path

from src.evidence_room import EvidenceRoom, RuleViolation, verify_chain
from src.evidence_room.types import (
    CaseStage,
    LinkStatus,
    MaterialCategory as C,
    Team,
)

H = "sha256:" + "0" * 64


def base_room() -> EvidenceRoom:
    """构造一个最小可办案环境：已立案、登记两人一假设、一份初始授权。"""
    r = EvidenceRoom()
    r.open_case("C-1", "虚构测试案件", "chief", "2026-03-01T09:00:00+08:00")
    r.register_subject("S-1", "虚构甲", "疑似对象", "lead-a",
                       timestamp="2026-03-01T10:00:00+08:00")
    r.register_subject("S-2", "虚构乙", "关联对象", "lead-a",
                       timestamp="2026-03-01T10:05:00+08:00")
    r.register_hypothesis(
        "H-1", "虚构甲开药后由虚构乙转售", ["S-1", "S-2"], "lead-a",
        timestamp="2026-03-01T11:00:00+08:00")
    r.grant_authorization(
        "A-1", [C.SETTLEMENT, C.SURVEILLANCE],
        [Team.LEAD, Team.SURVEILLANCE],
        "2026-03-01T00:00:00+08:00", "2026-06-30T23:59:59+08:00",
        "chief", basis="初查", timestamp="2026-03-02T09:00:00+08:00")
    return r


def at(s: str) -> datetime:
    return datetime.fromisoformat(s)


class HashChainTest(unittest.TestCase):
    def test_events_form_tamper_evident_chain(self):
        r = base_room()
        r.register_material(
            "M-1", C.SETTLEMENT, "结算明细", "来源甲", "2026-03-03T10:00:00+08:00",
            H, "A-1", "lead", "线索组柜", "lead-a",
            hypothesis_ids=["H-1"], timestamp="2026-03-03T11:00:00+08:00")
        verify_chain(r.events)  # 正常链可通过校验

        tampered = copy.deepcopy(r.events)
        tampered[0]["payload"]["title"] = "被覆盖的标题"
        with self.assertRaisesRegex(ValueError, "哈希不匹配"):
            EvidenceRoom(tampered)

        deleted = copy.deepcopy(r.events)
        deleted.pop(1)
        with self.assertRaisesRegex(ValueError, "序号不连续|前序哈希断裂|哈希不匹配"):
            EvidenceRoom(deleted)

    def test_decisions_cannot_be_overwritten(self):
        r = base_room()
        r.register_material(
            "M-1", C.SETTLEMENT, "结算明细", "来源甲", "2026-03-03T10:00:00+08:00",
            H, "A-1", "lead", "线索组柜", "lead-a",
            hypothesis_ids=["H-1"], timestamp="2026-03-03T11:00:00+08:00")
        with self.assertRaises(RuleViolation):
            r.register_material(
                "M-1", C.SETTLEMENT, "重复登记", "来源甲",
                "2026-03-03T10:00:00+08:00", H, "A-1", "lead",
                "线索组柜", "lead-a", hypothesis_ids=["H-1"],
                timestamp="2026-03-03T12:00:00+08:00")


class AuthorizationTest(unittest.TestCase):
    def test_material_requires_valid_authorization(self):
        r = base_room()
        with self.assertRaisesRegex(RuleViolation, "授权不存在"):
            r.register_material(
                "M-X", C.SETTLEMENT, "无授权材料", "来源",
                "2026-03-03T10:00:00+08:00", H, "A-NOPE", "lead",
                "柜", "lead-a", timestamp="2026-03-03T11:00:00+08:00")

        with self.assertRaisesRegex(RuleViolation, "不覆盖材料类别"):
            r.register_material(
                "M-X", C.FINANCE, "越类别材料", "来源",
                "2026-03-03T10:00:00+08:00", H, "A-1", "fund_flow",
                "柜", "lead-a", timestamp="2026-03-03T11:00:00+08:00")

        with self.assertRaisesRegex(RuleViolation, "不在有效期限"):
            r.register_material(
                "M-X", C.SETTLEMENT, "超期取得材料", "来源",
                "2026-07-01T10:00:00+08:00", H, "A-1", "lead",
                "柜", "lead-a", timestamp="2026-07-01T11:00:00+08:00")

    def test_stage_locked_authorization_blocks_collection(self):
        r = base_room()
        r.grant_authorization(
            "A-2", [C.FINANCE], [Team.FUND_FLOW],
            "2026-03-01T00:00:00+08:00", "2026-12-31T23:59:59+08:00",
            "chief", basis="印证阶段才可查资金",
            min_stage=CaseStage.CORROBORATION,
            timestamp="2026-03-02T09:30:00+08:00")
        with self.assertRaisesRegex(RuleViolation, "要求案件到达"):
            r.register_material(
                "M-F", C.FINANCE, "资金材料", "银行",
                "2026-03-05T10:00:00+08:00", H, "A-2", "fund_flow",
                "资金组柜", "fund-b", timestamp="2026-03-05T11:00:00+08:00")

    def test_supplemental_authorization_must_reference_existing(self):
        r = base_room()
        with self.assertRaisesRegex(RuleViolation, "被补充的授权不存在"):
            r.grant_authorization(
                "A-S", [C.LOGISTICS], [Team.LOGISTICS],
                "2026-03-01T00:00:00+08:00", "2026-12-31T23:59:59+08:00",
                "chief", basis="补充", supplemental_of="A-NOPE",
                timestamp="2026-03-02T10:00:00+08:00")


class EscalationTest(unittest.TestCase):
    def _settlement(self, r, mid="M-1"):
        r.register_material(
            mid, C.SETTLEMENT, "结算明细", "医保平台",
            "2026-03-03T10:00:00+08:00", H, "A-1", "lead", "线索组柜",
            "lead-a", hypothesis_ids=["H-1"],
            timestamp="2026-03-03T11:00:00+08:00")

    def test_single_record_cannot_escalate(self):
        r = base_room()
        self._settlement(r)
        with self.assertRaisesRegex(RuleViolation, "材料类别单一"):
            r.escalate_lead(
                "H-1", "chief", "仅凭同日开药",
                timestamp="2026-03-04T09:00:00+08:00")

    def test_multiple_categories_without_reviewed_link_cannot_escalate(self):
        r = base_room()
        self._settlement(r)
        r.register_material(
            "M-2", C.SURVEILLANCE, "监控片段", "医院安防",
            "2026-03-04T10:00:00+08:00", H, "A-1", "surveillance",
            "监控组柜", "surv-c", hypothesis_ids=["H-1"],
            timestamp="2026-03-04T11:00:00+08:00")
        r.propose_candidate_link(
            "L-1", "M-1", "M-2", "identity", "系统初判，未复核",
            "surv-c", confidence=0.7,
            timestamp="2026-03-05T09:00:00+08:00")
        with self.assertRaisesRegex(RuleViolation, "尚无经复核的联结"):
            r.escalate_lead(
                "H-1", "chief", "候选联结不能直接升级",
                timestamp="2026-03-05T10:00:00+08:00")

    def test_rejected_candidate_does_not_block_other_corroboration(self):
        r = base_room()
        self._settlement(r)
        r.register_material(
            "M-2", C.SETTLEMENT, "另一名正常患者同日结算", "医保平台",
            "2026-03-04T10:00:00+08:00", H, "A-1", "lead", "线索组柜",
            "lead-a", hypothesis_ids=["H-1"],
            timestamp="2026-03-04T10:30:00+08:00")
        r.register_material(
            "M-3", C.SURVEILLANCE, "监控片段", "医院安防",
            "2026-03-04T11:00:00+08:00", H, "A-1", "surveillance",
            "监控组柜", "surv-c", hypothesis_ids=["H-1"],
            timestamp="2026-03-04T12:00:00+08:00")
        r.propose_candidate_link(
            "L-BAD", "M-3", "M-2", "identity", "误报候选",
            "surv-c", timestamp="2026-03-05T09:00:00+08:00")
        r.propose_candidate_link(
            "L-OK", "M-3", "M-1", "identity", "与 M-1 对应",
            "surv-c", timestamp="2026-03-05T09:05:00+08:00")
        r.review_identity("L-BAD", LinkStatus.REJECTED, "chief",
                          "系另一患者，排除",
                          timestamp="2026-03-05T14:00:00+08:00")
        r.review_identity("L-OK", LinkStatus.CONFIRMED, "chief",
                          "签字与抓拍一致",
                          timestamp="2026-03-05T14:30:00+08:00")
        event = r.escalate_lead(
            "H-1", "chief", "排除正常患者后多源印证",
            timestamp="2026-03-06T09:00:00+08:00")
        self.assertEqual(event["kind"], "lead_escalated")
        # 升级决定不可重复、不可覆盖
        with self.assertRaisesRegex(RuleViolation, "已升级"):
            r.escalate_lead(
                "H-1", "chief", "再次升级",
                timestamp="2026-03-06T10:00:00+08:00")


class CandidateLinkTest(unittest.TestCase):
    def test_identity_not_merged_before_review(self):
        r = base_room()
        r.register_material(
            "M-1", C.SETTLEMENT, "结算明细", "医保平台",
            "2026-03-03T10:00:00+08:00", H, "A-1", "lead", "线索组柜",
            "lead-a", hypothesis_ids=["H-1"],
            timestamp="2026-03-03T11:00:00+08:00")
        r.register_material(
            "M-2", C.SURVEILLANCE, "监控片段", "医院安防",
            "2026-03-04T10:00:00+08:00", H, "A-1", "surveillance",
            "监控组柜", "surv-c", hypothesis_ids=["H-1"],
            timestamp="2026-03-04T11:00:00+08:00")
        r.propose_candidate_link(
            "L-1", "M-1", "M-2", "identity", "待核",
            "surv-c", timestamp="2026-03-05T09:00:00+08:00")
        self.assertEqual(r.identity_groups(), [])
        self.assertEqual(r.links["L-1"]["status"], LinkStatus.CANDIDATE)

        r.review_identity("L-1", LinkStatus.CONFIRMED, "chief", "人工比对一致",
                          timestamp="2026-03-05T15:00:00+08:00")
        self.assertEqual(r.identity_groups(), [["M-1", "M-2"]])

    def test_review_decision_is_final(self):
        r = base_room()
        r.register_material(
            "M-1", C.SETTLEMENT, "结算明细", "医保平台",
            "2026-03-03T10:00:00+08:00", H, "A-1", "lead", "柜",
            "lead-a", hypothesis_ids=["H-1"],
            timestamp="2026-03-03T11:00:00+08:00")
        r.register_material(
            "M-2", C.SURVEILLANCE, "监控片段", "医院安防",
            "2026-03-04T10:00:00+08:00", H, "A-1", "surveillance",
            "柜", "surv-c", hypothesis_ids=["H-1"],
            timestamp="2026-03-04T11:00:00+08:00")
        r.propose_candidate_link(
            "L-1", "M-1", "M-2", "identity", "待核",
            "surv-c", timestamp="2026-03-05T09:00:00+08:00")
        r.review_identity("L-1", LinkStatus.REJECTED, "chief", "不是同一人",
                          timestamp="2026-03-05T15:00:00+08:00")
        with self.assertRaisesRegex(RuleViolation, "已经复核"):
            r.review_identity("L-1", LinkStatus.CONFIRMED, "chief", "想改结论",
                              timestamp="2026-03-05T16:00:00+08:00")


class VisibilityTest(unittest.TestCase):
    def _room_with_finance(self) -> EvidenceRoom:
        r = base_room()
        r.register_material(
            "M-1", C.SETTLEMENT, "结算明细", "医保平台",
            "2026-03-03T10:00:00+08:00", H, "A-1", "lead", "线索组柜",
            "lead-a", hypothesis_ids=["H-1"],
            timestamp="2026-03-03T11:00:00+08:00")
        r.register_material(
            "M-2", C.SURVEILLANCE, "监控片段", "医院安防",
            "2026-03-04T10:00:00+08:00", H, "A-1", "surveillance",
            "监控组柜", "surv-c", hypothesis_ids=["H-1"],
            timestamp="2026-03-04T11:00:00+08:00")
        r.propose_candidate_link(
            "L-1", "M-1", "M-2", "identity", "待核",
            "surv-c", timestamp="2026-03-05T09:00:00+08:00")
        r.review_identity("L-1", LinkStatus.CONFIRMED, "chief", "一致",
                          timestamp="2026-03-05T15:00:00+08:00")
        r.submit_stage_conclusion(
            CaseStage.INITIAL,
            facts=[{"fact_id": "F-1", "statement": "同一人",
                    "material_refs": ["M-1", "M-2"], "link_refs": ["L-1"]}],
            pending=[], actor="chief",
            timestamp="2026-03-08T17:00:00+08:00")
        r.grant_authorization(
            "A-2", [C.FINANCE], [Team.FUND_FLOW],
            "2026-03-09T00:00:00+08:00", "2026-12-31T23:59:59+08:00",
            "chief", basis="印证阶段", min_stage=CaseStage.CORROBORATION,
            timestamp="2026-03-09T09:00:00+08:00")
        r.register_material(
            "M-3", C.FINANCE, "资金流水", "银行",
            "2026-03-10T10:00:00+08:00", H, "A-2", "fund_flow", "资金组柜",
            "fund-b", hypothesis_ids=["H-1"],
            timestamp="2026-03-10T11:00:00+08:00")
        return r

    def test_team_sees_only_need_to_know_materials(self):
        r = self._room_with_finance()
        fund_ids = {m["material_id"] for m in
                    r.view_for(Team.FUND_FLOW, at("2026-03-10T12:00:00+08:00"))["materials"]}
        surv_ids = {m["material_id"] for m in
                    r.view_for(Team.SURVEILLANCE, at("2026-03-10T12:00:00+08:00"))["materials"]}
        self.assertIn("M-3", fund_ids)
        self.assertNotIn("M-3", surv_ids)  # 监控组看不到资金材料
        self.assertIn("M-2", surv_ids)
        self.assertNotIn("M-2", fund_ids)  # 资金组看不到监控

    def test_loan_window_grants_temporary_cross_team_access(self):
        r = self._room_with_finance()
        r.grant_loan(
            "LN-1", "M-3", Team.SURVEILLANCE, "核对一笔转账画面",
            "2026-03-11T09:00:00+08:00", "2026-03-18T18:00:00+08:00",
            "chief", timestamp="2026-03-11T08:30:00+08:00")
        during = r.view_for(Team.SURVEILLANCE, at("2026-03-12T10:00:00+08:00"))
        self.assertIn("M-3", {m["material_id"] for m in during["materials"]})
        after = r.view_for(Team.SURVEILLANCE, at("2026-03-19T10:00:00+08:00"))
        self.assertNotIn("M-3", {m["material_id"] for m in after["materials"]})

    def test_parallel_analysis_respects_visibility(self):
        r = self._room_with_finance()
        with self.assertRaisesRegex(RuleViolation, "没有履职所需的可见权限"):
            r.record_parallel_analysis(
                "A-X", Team.SURVEILLANCE, ["M-3"], "越权分析资金",
                "surv-c", timestamp="2026-03-11T10:00:00+08:00")
        r.record_parallel_analysis(
            "A-1", Team.FUND_FLOW, ["M-3"], "转账频次异常",
            "fund-b", timestamp="2026-03-11T10:00:00+08:00")

    def test_sealed_material_cannot_be_loaned(self):
        r = self._room_with_finance()
        # M-1 已随初查结论冻结
        with self.assertRaisesRegex(RuleViolation, "已随阶段结论冻结"):
            r.grant_loan(
                "LN-2", "M-1", Team.FUND_FLOW, "借阅已冻结结算",
                "2026-03-11T09:00:00+08:00", "2026-03-20T18:00:00+08:00",
                "chief", timestamp="2026-03-11T08:30:00+08:00")


class InvalidationTest(unittest.TestCase):
    def test_invalidation_keeps_history(self):
        r = base_room()
        r.register_material(
            "M-1", C.SETTLEMENT, "来源存疑截图", "匿名附件",
            "2026-03-03T10:00:00+08:00", H, "A-1", "lead", "线索组柜",
            "lead-a", hypothesis_ids=["H-1"],
            timestamp="2026-03-03T11:00:00+08:00")
        kinds_before = [e["kind"] for e in r.events]
        r.invalidate_material("M-1", "来源无法核验", "chief",
                              timestamp="2026-03-06T09:00:00+08:00")
        kinds_after = [e["kind"] for e in r.events]
        self.assertEqual(kinds_after[:len(kinds_before)], kinds_before)
        self.assertIn("material_invalidated", kinds_after)
        self.assertFalse(r.materials["M-1"]["valid"])

        with self.assertRaisesRegex(RuleViolation, "失效材料不得外借"):
            r.grant_loan(
                "LN-1", "M-1", Team.REVIEW, "借阅失效件",
                "2026-03-07T09:00:00+08:00", "2026-03-10T18:00:00+08:00",
                "chief", timestamp="2026-03-07T08:30:00+08:00")

    def test_invalid_material_cannot_support_fact(self):
        r = base_room()
        r.register_material(
            "M-1", C.SETTLEMENT, "结算明细", "医保平台",
            "2026-03-03T10:00:00+08:00", H, "A-1", "lead", "柜",
            "lead-a", hypothesis_ids=["H-1"],
            timestamp="2026-03-03T11:00:00+08:00")
        r.register_material(
            "M-2", C.SURVEILLANCE, "监控片段", "医院安防",
            "2026-03-04T10:00:00+08:00", H, "A-1", "surveillance",
            "柜", "surv-c", hypothesis_ids=["H-1"],
            timestamp="2026-03-04T11:00:00+08:00")
        r.propose_candidate_link(
            "L-1", "M-1", "M-2", "identity", "待核",
            "surv-c", timestamp="2026-03-05T09:00:00+08:00")
        r.review_identity("L-1", LinkStatus.CONFIRMED, "chief", "一致",
                          timestamp="2026-03-05T15:00:00+08:00")
        r.invalidate_material("M-2", "片段被证明时间错位", "chief",
                              timestamp="2026-03-06T09:00:00+08:00")
        with self.assertRaisesRegex(RuleViolation, "失效材料"):
            r.submit_stage_conclusion(
                CaseStage.INITIAL,
                facts=[{"fact_id": "F-1", "statement": "同一人",
                        "material_refs": ["M-1", "M-2"],
                        "link_refs": ["L-1"]}],
                pending=[], actor="chief",
                timestamp="2026-03-08T17:00:00+08:00")


class ConclusionTest(unittest.TestCase):
    def _confirmed_pair(self) -> EvidenceRoom:
        r = base_room()
        r.register_material(
            "M-1", C.SETTLEMENT, "结算明细", "医保平台",
            "2026-03-03T10:00:00+08:00", "sha256:" + "a" * 64,
            "A-1", "lead", "线索组柜", "lead-a",
            hypothesis_ids=["H-1"], timestamp="2026-03-03T11:00:00+08:00")
        r.register_material(
            "M-2", C.SURVEILLANCE, "监控片段", "医院安防",
            "2026-03-04T10:00:00+08:00", "sha256:" + "b" * 64,
            "A-1", "surveillance", "监控组柜", "surv-c",
            hypothesis_ids=["H-1"], timestamp="2026-03-04T11:00:00+08:00")
        r.propose_candidate_link(
            "L-1", "M-1", "M-2", "identity", "待核",
            "surv-c", timestamp="2026-03-05T09:00:00+08:00")
        return r

    def test_uncorroborated_fact_rejected(self):
        r = self._confirmed_pair()
        # 联结仍是候选：不能写成已印证事实
        with self.assertRaisesRegex(RuleViolation, "缺少已复核联结"):
            r.submit_stage_conclusion(
                CaseStage.INITIAL,
                facts=[{"fact_id": "F-1", "statement": "同一人",
                        "material_refs": ["M-1", "M-2"]}],
                pending=[], actor="chief",
                timestamp="2026-03-08T17:00:00+08:00")
        # 单一类别也不叫相互印证：用两份结算材料触发
        r.register_material(
            "M-3", C.SETTLEMENT, "另一份结算", "医保平台",
            "2026-03-05T10:00:00+08:00", "sha256:" + "c" * 64,
            "A-1", "lead", "线索组柜", "lead-a",
            hypothesis_ids=["H-1"], timestamp="2026-03-05T11:00:00+08:00")
        with self.assertRaisesRegex(RuleViolation, "单一类别"):
            r.submit_stage_conclusion(
                CaseStage.INITIAL,
                facts=[{"fact_id": "F-1", "statement": "只有结算",
                        "material_refs": ["M-1", "M-3"]}],
                pending=[], actor="chief",
                timestamp="2026-03-08T17:00:00+08:00")

    def test_pending_must_reference_candidate_link(self):
        r = self._confirmed_pair()
        r.review_identity("L-1", LinkStatus.CONFIRMED, "chief", "一致",
                          timestamp="2026-03-05T15:00:00+08:00")
        with self.assertRaisesRegex(RuleViolation, "已复核，应作为事实或排除项"):
            r.submit_stage_conclusion(
                CaseStage.INITIAL, facts=[],
                pending=[{"item_id": "P-1", "statement": "仍待核？",
                          "link_refs": ["L-1"]}],
                actor="chief", timestamp="2026-03-08T17:00:00+08:00")

    def test_freeze_captures_versions_and_later_transfers_show_in_report(self):
        r = self._confirmed_pair()
        r.review_identity("L-1", LinkStatus.CONFIRMED, "chief", "一致",
                          timestamp="2026-03-05T15:00:00+08:00")
        r.submit_stage_conclusion(
            CaseStage.INITIAL,
            facts=[{"fact_id": "F-1", "statement": "同一人",
                    "material_refs": ["M-1", "M-2"], "link_refs": ["L-1"]}],
            pending=[], actor="chief",
            timestamp="2026-03-08T17:00:00+08:00")
        # 阶段已推进，不能重复提交初查结论
        with self.assertRaises(RuleViolation):
            r.submit_stage_conclusion(
                CaseStage.INITIAL, facts=[], pending=[], actor="chief",
                timestamp="2026-03-08T18:00:00+08:00")

        # 冻结后仍可移交，轨迹进入报告
        r.transfer_material("M-1", "负责人档案柜", "chief",
                            "随结论移送",
                            timestamp="2026-03-09T09:00:00+08:00")
        report = r.conclusion_report()
        self.assertEqual(report["stage"], CaseStage.INITIAL)
        frozen = {f["material_id"]: f for f in report["frozen_materials"]}
        self.assertEqual(frozen["M-1"]["custodian_at_freeze"], "线索组柜")
        self.assertEqual(frozen["M-1"]["custodian_now"], "负责人档案柜")
        self.assertFalse(frozen["M-1"]["version_changed"])
        moved = frozen["M-1"]["transferred_after_freeze"]
        self.assertEqual(len(moved), 1)
        self.assertEqual(moved[0]["custodian"], "负责人档案柜")

        trail = r.custody_trail("M-1")
        self.assertEqual([t["reason"] for t in trail], ["登记入室", "随结论移送"])

    def test_stage_advances_through_conclusions(self):
        r = self._confirmed_pair()
        r.review_identity("L-1", LinkStatus.CONFIRMED, "chief", "一致",
                          timestamp="2026-03-05T15:00:00+08:00")
        self.assertEqual(r.stage, CaseStage.INITIAL)
        r.submit_stage_conclusion(
            CaseStage.INITIAL,
            facts=[{"fact_id": "F-1", "statement": "同一人",
                    "material_refs": ["M-1", "M-2"], "link_refs": ["L-1"]}],
            pending=[], actor="chief",
            timestamp="2026-03-08T17:00:00+08:00")
        self.assertEqual(r.stage, CaseStage.CORROBORATION)


class SerializationTest(unittest.TestCase):
    def test_roundtrip_and_bad_envelope(self):
        r = base_room()
        data = r.to_dict()
        raw = json.dumps(data)
        r2 = EvidenceRoom.from_dict(json.loads(raw))
        self.assertEqual(len(r2.events), len(r.events))

        bad_domain = json.loads(raw)
        bad_domain["domain"] = "something-else"
        with self.assertRaises(ValueError):
            EvidenceRoom.from_dict(bad_domain)
        bad_version = json.loads(raw)
        bad_version["log_version"] = 99
        with self.assertRaises(ValueError):
            EvidenceRoom.from_dict(bad_version)


class FixtureCaseTest(unittest.TestCase):
    """对全流程虚构样例案件的端到端校验。"""

    @classmethod
    def setUpClass(cls):
        path = Path("fixtures/case.json")
        cls.data = json.loads(path.read_text(encoding="utf-8"))
        cls.room = EvidenceRoom.from_dict(cls.data)

    def test_chain_intact(self):
        verify_chain(self.room.events)

    def test_case_progressed_to_filing(self):
        self.assertEqual(self.room.case["case_id"], "YB-2026-051X")
        self.assertEqual(self.room.stage, CaseStage.CASE_FILING)
        self.assertEqual(len(self.room.conclusions), 2)

    def test_identity_only_merged_after_review(self):
        # 3 月、4 月两段监控经复核后才与结算并为同一身份组
        self.assertEqual(
            self.room.identity_groups(),
            [["M-SETTLE-01", "M-VIDEO-01", "M-VIDEO-02"]])
        # 王丙候选被排除，状态不可覆盖
        self.assertEqual(self.room.links["L-02"]["status"], LinkStatus.REJECTED)
        # 第二收件地址的联结始终只是待核关联
        self.assertEqual(self.room.links["L-07"]["status"], LinkStatus.CANDIDATE)

    def test_conclusions_separate_facts_and_pending(self):
        first = self.room.conclusions[0]
        self.assertEqual([f["fact_id"] for f in first["facts"]], ["F-INIT-1"])
        self.assertEqual([p["item_id"] for p in first["pending"]], ["P-INIT-1"])
        second = self.room.conclusion_report(-1)
        statements = {f["fact_id"] for f in second["corroborated_facts"]}
        self.assertEqual(statements, {"F-CORR-1", "F-CORR-2"})
        self.assertEqual(
            [p["item_id"] for p in second["pending_associations"]],
            ["P-CORR-1"])

    def test_invalidated_material_kept_in_log_but_excluded(self):
        self.assertIn("material_invalidated",
                      [e["kind"] for e in self.room.events])
        self.assertFalse(self.room.materials["M-FIN-02"]["valid"])
        for conclusion in self.room.conclusions:
            used = {ref for f in conclusion["facts"]
                    for ref in f["material_refs"]}
            self.assertNotIn("M-FIN-02", used)

    def test_loan_window_then_seal(self):
        # 借阅窗内监控组可见追溯材料
        inside = self.room.view_for(Team.SURVEILLANCE,
                                    at("2026-04-15T10:00:00+08:00"))
        self.assertIn("M-TRACE-01",
                      {m["material_id"] for m in inside["materials"]})
        # 窗外恢复不可见
        outside = self.room.view_for(Team.SURVEILLANCE,
                                     at("2026-05-01T10:00:00+08:00"))
        self.assertNotIn("M-TRACE-01",
                         {m["material_id"] for m in outside["materials"]})
        # 已冻结材料不能再借
        with self.assertRaisesRegex(RuleViolation, "已随阶段结论冻结"):
            self.room.grant_loan(
                "LN-AFTER", "M-TRACE-01", Team.SURVEILLANCE, "冻结后借阅",
                "2026-05-02T09:00:00+08:00", "2026-05-09T18:00:00+08:00",
                "chief", timestamp="2026-05-02T08:30:00+08:00")

    def test_report_shows_post_freeze_transfers(self):
        report = self.room.conclusion_report(-1)
        frozen = {f["material_id"]: f for f in report["frozen_materials"]}
        for mid in ("M-FIN-01", "M-LOG-01"):
            self.assertEqual(frozen[mid]["custodian_now"], "负责人复核档案柜")
            self.assertEqual(
                len(frozen[mid]["transferred_after_freeze"]), 1)
        # 冻结摘要均未发生版本变化
        self.assertTrue(all(not f["version_changed"]
                            for f in report["frozen_materials"]))

    def test_rebuild_from_scratch_matches(self):
        rebuilt = EvidenceRoom([copy.deepcopy(e) for e in self.data["events"]])
        self.assertEqual(rebuilt.stage, self.room.stage)
        self.assertEqual(len(rebuilt.materials), len(self.room.materials))
        self.assertEqual(
            {mid: m["custodian"] for mid, m in rebuilt.materials.items()},
            {mid: m["custodian"] for mid, m in self.room.materials.items()})


if __name__ == "__main__":
    unittest.main()
