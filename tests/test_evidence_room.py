import unittest
from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone

from src.evidence_room import (
    Authorization,
    AuthorizationError,
    CaseStage,
    DecisionKind,
    EvidenceRoom,
    Finding,
    InvalidStateError,
    LinkStatus,
    NotFoundError,
    ReviewError,
    StageError,
)

T0 = datetime(2026, 1, 1, 9, 0, tzinfo=timezone.utc)


def at(hours=0):
    return T0 + timedelta(hours=hours)


class EvidenceRoomTestBase(unittest.TestCase):
    def setUp(self):
        self.room = EvidenceRoom()
        self.room.open_case("YB-2026-001", lead="负责人甲")
        self.room.grant_authorization(
            Authorization(
                auth_id="AUTH-1",
                case_id="YB-2026-001",
                granted_to="数据分析组",
                sources=frozenset({"医保结算系统", "医院监控"}),
                scope=frozenset({"内部分析", "cross-dept"}),
                valid_from=T0 - timedelta(days=30),
                valid_until=T0 + timedelta(days=30),
            ),
            actor="负责人甲",
            at=at(),
        )

    def register(self, kind="settlement", source="医保结算系统", content=b"data",
                 scope=("内部分析",), supports=(), auth="AUTH-1", hours=1):
        return self.room.register_material(
            "YB-2026-001",
            actor="数据分析组",
            kind=kind,
            source=source,
            content=content,
            acquired_at=at(hours),
            scope=scope,
            authorization_id=auth,
            supports=supports,
            at=at(hours),
        )


class RegistrationTest(EvidenceRoomTestBase):
    def test_register_material_records_digest_and_decision(self):
        mid = self.register(content=b"settlement-rows")
        material = self.room._materials[mid]
        self.assertEqual(material.current.version, 1)
        self.assertEqual(len(material.current.digest), 64)
        kinds = [e.kind for e in self.room.decision_log("YB-2026-001")]
        self.assertIn(DecisionKind.REGISTRATION, kinds)

    def test_register_requires_known_authorization(self):
        with self.assertRaises(AuthorizationError):
            self.register(auth="AUTH-X")

    def test_register_rejects_source_outside_authorization(self):
        with self.assertRaises(AuthorizationError):
            self.register(source="银行流水")

    def test_register_rejects_scope_outside_authorization(self):
        with self.assertRaises(AuthorizationError):
            self.register(scope=("公开发布",))

    def test_register_rejects_acquired_at_outside_validity(self):
        with self.assertRaises(AuthorizationError):
            self.register(hours=24 * 60)  # 超出授权有效期

    def test_closed_case_rejects_registration(self):
        self.room.escalate("YB-2026-001", "负责人甲", CaseStage.PRELIMINARY,
                           "线索成立", at=at(2))
        self.room.escalate("YB-2026-001", "负责人甲", CaseStage.FORMAL,
                           "正式立案", at=at(3))
        self.room.escalate("YB-2026-001", "负责人甲", CaseStage.CLOSED,
                           "结案", at=at(4))
        with self.assertRaises(InvalidStateError):
            self.register(hours=5)


class CorrespondenceTest(EvidenceRoomTestBase):
    def setUp(self):
        super().setUp()
        self.video = self.register(kind="surveillance", source="医院监控",
                                   content=b"video-clip", hours=1)
        self.settlement = self.register(kind="settlement", content=b"rows",
                                        hours=2)

    def test_candidate_does_not_merge_identity_until_reviewed(self):
        link_id = self.room.propose_correspondence(
            "YB-2026-001", "数据分析组", self.video, self.settlement,
            note="同日同窗口取药", at=at(3),
        )
        # 未经复核：身份合并视图为空
        self.assertEqual(self.room.identity_links("YB-2026-001"), ())
        self.assertEqual(self.room._links[link_id].status, LinkStatus.CANDIDATE)

        self.room.review_correspondence(link_id, "复核人乙", approve=True,
                                        note="人脸与就诊卡一致", at=at(4))
        confirmed = self.room.identity_links("YB-2026-001")
        self.assertEqual(len(confirmed), 1)
        self.assertEqual(confirmed[0].reviewed_by, "复核人乙")

    def test_proposer_cannot_self_review(self):
        link_id = self.room.propose_correspondence(
            "YB-2026-001", "数据分析组", self.video, self.settlement, at=at(3),
        )
        with self.assertRaises(ReviewError):
            self.room.review_correspondence(link_id, "数据分析组", approve=True,
                                            at=at(4))

    def test_double_review_rejected(self):
        link_id = self.room.propose_correspondence(
            "YB-2026-001", "数据分析组", self.video, self.settlement, at=at(3),
        )
        self.room.review_correspondence(link_id, "复核人乙", approve=False,
                                        at=at(4))
        with self.assertRaises(ReviewError):
            self.room.review_correspondence(link_id, "复核人丙", approve=True,
                                            at=at(5))
        self.assertEqual(self.room.identity_links("YB-2026-001"), ())

    def test_propose_rejects_unknown_material(self):
        with self.assertRaises(NotFoundError):
            self.room.propose_correspondence(
                "YB-2026-001", "数据分析组", self.video, "YB-2026-001-M9999",
                at=at(3),
            )


class DecisionLogTest(EvidenceRoomTestBase):
    def test_log_is_append_only_and_hash_chained(self):
        mid = self.register(content=b"v1")
        self.room.update_material(mid, "数据分析组", b"v2", at=at(2))
        self.room.record_analysis("YB-2026-001", "数据分析组",
                                  "同日同人多院开药", [mid], at=at(3))
        self.room.escalate("YB-2026-001", "负责人甲", CaseStage.PRELIMINARY,
                           "多条线索汇聚", at=at(4))
        self.assertTrue(self.room.verify_log())

        entries = self.room.decision_log("YB-2026-001")
        kinds = [e.kind for e in entries]
        self.assertEqual(
            kinds,
            [DecisionKind.AUTH_GRANT, DecisionKind.REGISTRATION,
             DecisionKind.VERSION, DecisionKind.ANALYSIS,
             DecisionKind.ESCALATION],
        )
        # 条目为不可变对象，不可覆盖
        with self.assertRaises(FrozenInstanceError):
            entries[0].actor = "篡改者"  # type: ignore[misc]

    def test_tampering_breaks_verification(self):
        self.register()
        entry = self.room.decision_log()[0]
        forged = type(entry)(
            seq=entry.seq, case_id=entry.case_id, kind=entry.kind,
            actor="篡改者", detail=entry.detail, at=entry.at,
            prev_hash=entry.prev_hash, hash=entry.hash,
        )
        self.room._log[0] = forged
        self.assertFalse(self.room.verify_log())


class StageAccessTest(EvidenceRoomTestBase):
    def setUp(self):
        super().setUp()
        self.room.grant_authorization(
            Authorization(
                auth_id="AUTH-2",
                case_id="YB-2026-001",
                granted_to="外勤组",
                sources=frozenset({"医院监控", "物流单据"}),
                scope=frozenset({"内部分析"}),
                valid_from=T0 - timedelta(days=30),
            ),
            actor="负责人甲",
            at=at(),
        )
        self.settlement = self.register(kind="settlement", hours=1)
        self.video = self.register(kind="surveillance", source="医院监控",
                                   auth="AUTH-2", hours=2)

    def kinds_visible_to(self, group):
        return {m.kind for m in self.room.visible_materials("YB-2026-001", group)}

    def test_groups_see_only_need_to_know_at_lead_stage(self):
        self.assertEqual(self.kinds_visible_to("数据分析组"), {"settlement"})
        self.assertEqual(self.kinds_visible_to("外勤组"), {"surveillance"})
        self.assertEqual(self.kinds_visible_to("法制审核组"), set())
        self.assertEqual(self.kinds_visible_to("陌生小组"), set())
        # 负责人全量可见
        self.assertEqual(self.kinds_visible_to("负责人甲"),
                         {"settlement", "surveillance"})

    def test_visibility_widens_after_escalation(self):
        self.room.escalate("YB-2026-001", "负责人甲", CaseStage.PRELIMINARY,
                           "线索成立", at=at(3))
        self.assertEqual(self.kinds_visible_to("外勤组"),
                         {"settlement", "surveillance"})
        self.assertEqual(self.kinds_visible_to("法制审核组"),
                         {"settlement", "surveillance"})

    def test_escalation_only_forward_and_lead_only(self):
        with self.assertRaises(AuthorizationError):
            self.room.escalate("YB-2026-001", "数据分析组",
                               CaseStage.PRELIMINARY, "越权", at=at(3))
        self.room.escalate("YB-2026-001", "负责人甲", CaseStage.PRELIMINARY,
                           "线索成立", at=at(3))
        with self.assertRaises(StageError):
            self.room.escalate("YB-2026-001", "负责人甲", CaseStage.LEAD,
                               "回退", at=at(4))

    def test_invalidated_material_hidden_from_groups(self):
        self.room.invalidate(self.settlement, "负责人甲", "来源存疑", at=at(3))
        self.assertEqual(self.kinds_visible_to("数据分析组"), set())
        # 负责人保留审计视图
        self.assertEqual(self.kinds_visible_to("负责人甲"), {"settlement", "surveillance"})


class SupplementaryAuthorizationTest(EvidenceRoomTestBase):
    def test_supplementary_authorization_logged(self):
        self.room.grant_authorization(
            Authorization(
                auth_id="AUTH-2",
                case_id="YB-2026-001",
                granted_to="数据分析组",
                sources=frozenset({"银行流水"}),
                scope=frozenset({"内部分析"}),
                valid_from=T0,
                supplementary_of="AUTH-1",
            ),
            actor="负责人甲",
            at=at(1),
        )
        kinds = [e.kind for e in self.room.decision_log("YB-2026-001")]
        self.assertIn(DecisionKind.SUPPLEMENTARY_AUTH, kinds)

    def test_supplementary_requires_existing_base(self):
        with self.assertRaises(NotFoundError):
            self.room.grant_authorization(
                Authorization(
                    auth_id="AUTH-3",
                    case_id="YB-2026-001",
                    granted_to="数据分析组",
                    sources=frozenset({"银行流水"}),
                    scope=frozenset({"内部分析"}),
                    valid_from=T0,
                    supplementary_of="AUTH-X",
                ),
                actor="负责人甲",
                at=at(1),
            )


class InvalidationAndBorrowTest(EvidenceRoomTestBase):
    def test_invalidation_blocks_borrow_update_and_conclusion(self):
        mid = self.register(scope=("内部分析", "cross-dept"))
        self.room.invalidate(mid, "负责人甲", "取得程序瑕疵", at=at(2))
        with self.assertRaises(InvalidStateError):
            self.room.borrow(mid, "外勤组", "公安经侦", "协查", at=at(3))
        with self.assertRaises(InvalidStateError):
            self.room.update_material(mid, "数据分析组", b"v2", at=at(3))
        with self.assertRaises(InvalidStateError):
            self.room.submit_conclusion(
                "YB-2026-001", "负责人甲",
                [Finding("事实", (mid,))], at=at(3),
            )
        kinds = [e.kind for e in self.room.decision_log("YB-2026-001")]
        self.assertIn(DecisionKind.INVALIDATION, kinds)

    def test_borrow_requires_scope_and_records_custody(self):
        restricted = self.register(scope=("内部分析",))
        with self.assertRaises(AuthorizationError):
            self.room.borrow(restricted, "外勤组", "公安经侦", "协查", at=at(2))

        shareable = self.register(scope=("内部分析", "cross-dept"), hours=3)
        self.room.borrow(shareable, "外勤组", "公安经侦", "协查比对", at=at(4))
        self.room.borrow(shareable, "法制审核组", "检察院", "移送审查", at=at(5))
        custody = self.room._materials[shareable].custody
        self.assertEqual([t.department for t in custody],
                         ["公安经侦", "检察院"])
        kinds = [e.kind for e in self.room.decision_log("YB-2026-001")]
        self.assertEqual(kinds.count(DecisionKind.BORROW), 2)


class ConclusionTest(EvidenceRoomTestBase):
    def setUp(self):
        super().setUp()
        self.room.grant_authorization(
            Authorization(
                auth_id="AUTH-2",
                case_id="YB-2026-001",
                granted_to="外勤组",
                sources=frozenset({"医院监控"}),
                scope=frozenset({"内部分析", "cross-dept"}),
                valid_from=T0 - timedelta(days=30),
            ),
            actor="负责人甲",
            at=at(),
        )
        self.m1 = self.register(kind="settlement", content=b"rows",
                                supports=("f1",), hours=1)
        self.m2 = self.register(kind="surveillance", source="医院监控",
                                content=b"video", auth="AUTH-2",
                                scope=("内部分析", "cross-dept"),
                                supports=("f1",), hours=2)
        self.m3 = self.register(kind="settlement", content=b"rows-2",
                                supports=("f2",), hours=3)

    def test_only_lead_submits_and_versions_are_frozen(self):
        with self.assertRaises(AuthorizationError):
            self.room.submit_conclusion(
                "YB-2026-001", "数据分析组",
                [Finding("事实", (self.m1,))], at=at(4),
            )

        cid = self.room.submit_conclusion(
            "YB-2026-001", "负责人甲",
            [Finding("同一患者同日多院结算", (self.m1, self.m2))],
            at=at(4),
        )
        frozen_digest = self.room._conclusions[cid].frozen[self.m1].digest

        # 结论后材料更新产生新版本，冻结版本不受影响
        self.room.update_material(self.m1, "数据分析组", b"rows-v2", at=at(5))
        report = self.room.conclusion_report(cid)
        self.assertEqual(report["version_status"][self.m1]["status"], "updated")
        self.assertEqual(report["version_status"][self.m1]["frozen_digest"],
                         frozen_digest)
        self.assertEqual(report["version_status"][self.m2]["status"], "unchanged")

    def test_report_distinguishes_corroborated_and_pending(self):
        link_id = self.room.propose_correspondence(
            "YB-2026-001", "数据分析组", self.m2, self.m1,
            note="视频与结算同日", at=at(4),
        )
        self.room.review_correspondence(link_id, "复核人乙", approve=True,
                                        at=at(5))
        pending_link = self.room.propose_correspondence(
            "YB-2026-001", "数据分析组", self.m2, self.m3,
            note="疑似同人待核", at=at(6),
        )
        self.room.borrow(self.m2, "外勤组", "公安经侦", "协查", at=at(7))

        cid = self.room.submit_conclusion(
            "YB-2026-001", "负责人甲",
            [
                Finding("同一患者同日多院结算取药", (self.m1, self.m2)),
                Finding("资金流向待查", (self.m3,)),
            ],
            at=at(8),
        )
        report = self.room.conclusion_report(cid)

        # 两个独立来源 -> 已相互印证；单一来源 -> 待核
        self.assertEqual([f["fact"] for f in report["corroborated_facts"]],
                         ["同一患者同日多院结算取药"])
        self.assertEqual([f["fact"] for f in report["pending_facts"]],
                         ["资金流向待查"])

        # 已确认对应与待核关联分列
        self.assertEqual([l["link_id"] for l in report["confirmed_links"]],
                        [link_id])
        self.assertEqual([l["link_id"] for l in report["pending_links"]],
                         [pending_link])

        # 每份材料后来被谁移交
        self.assertEqual(
            [t.department for t in report["custody"][self.m2]],
            ["公安经侦"],
        )
        self.assertEqual(report["custody"][self.m1], [])

    def test_invalidated_after_conclusion_marked_in_report(self):
        cid = self.room.submit_conclusion(
            "YB-2026-001", "负责人甲",
            [Finding("同一患者同日多院结算取药", (self.m1, self.m2))],
            at=at(4),
        )
        self.room.invalidate(self.m2, "负责人甲", "监控时段不符", at=at(5))
        report = self.room.conclusion_report(cid)
        self.assertEqual(report["version_status"][self.m2]["status"],
                         "invalidated")
        # 失效后不再计入相互印证
        self.assertEqual(report["corroborated_facts"], [])
        self.assertEqual(len(report["pending_facts"]), 1)


class RelationAndAnalysisTest(EvidenceRoomTestBase):
    def test_relation_registered_under_hypothesis(self):
        self.room.register_relation(
            "YB-2026-001", "数据分析组",
            hypothesis="H1：疑似倒药链条",
            subject="就诊人A", predicate="同日开药", obj="就诊人B",
            at=at(1),
        )
        entry = self.room.decision_log("YB-2026-001")[-1]
        self.assertEqual(entry.kind, DecisionKind.RELATION)
        self.assertEqual(entry.detail["hypothesis"], "H1：疑似倒药链条")

    def test_parallel_analysis_entries_do_not_overwrite(self):
        self.room.record_analysis("YB-2026-001", "数据分析组",
                                  "结算频次异常", at=at(1))
        self.room.record_analysis("YB-2026-001", "外勤组",
                                  "取药人行踪分析", at=at(2))
        entries = [e for e in self.room.decision_log("YB-2026-001")
                   if e.kind is DecisionKind.ANALYSIS]
        self.assertEqual(len(entries), 2)
        self.assertEqual({e.actor for e in entries}, {"数据分析组", "外勤组"})
        self.assertTrue(self.room.verify_log())


if __name__ == "__main__":
    unittest.main()
