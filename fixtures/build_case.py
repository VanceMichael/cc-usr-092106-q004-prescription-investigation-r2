"""生成虚构样例案件 fixtures/case.json。

覆盖完整办案脉络：假设登记、授权（含补充授权）、五类材料登记、
候选对应与复核（含排除正常患者）、线索升级、并行分析、材料失效、
跨部门借阅、两阶段结论冻结、冻结后移交。

所有人名、单位、单据号与时间均为虚构，不对应真实案件；
运行：python fixtures/build_case.py
"""

import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.evidence_room import EvidenceRoom
from src.evidence_room.types import (
    CaseStage,
    LinkStatus,
    MaterialCategory as C,
    Team,
)


def csum(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def build() -> EvidenceRoom:
    r = EvidenceRoom()

    # 1. 立案与对象 ----------------------------------------------------
    r.open_case("YB-2026-051X", "异常开药及疑似转售调查（虚构示例）",
                "review-zheng", "2026-03-01T09:00:00+08:00")
    r.register_subject("S-01", "张甲（虚构化名）", "疑似违规参保人",
                       "lead-zhou", note="同日开药疑点名单对象",
                       timestamp="2026-03-02T10:00:00+08:00")
    r.register_subject("S-02", "李乙（虚构化名）", "疑似收药转卖人",
                       "lead-zhou", timestamp="2026-03-02T10:05:00+08:00")
    r.register_subject("S-03", "王丙（虚构化名）", "需排除的同日开药患者",
                       "lead-zhou", timestamp="2026-03-02T10:10:00+08:00")
    r.register_hypothesis(
        "H-01",
        "张甲疑似长期超量开取降糖药 X，经李乙回收并转售外地",
        ["S-01", "S-02"], "lead-zhou",
        basis="同日开药疑点名单仅为线索，需多源材料印证",
        timestamp="2026-03-02T11:00:00+08:00")

    # 2. 初始授权（结算 + 监控） ---------------------------------------
    r.grant_authorization(
        "AUTH-01", [C.SETTLEMENT, C.SURVEILLANCE],
        [Team.LEAD, Team.SURVEILLANCE],
        "2026-03-01T00:00:00+08:00", "2026-06-30T23:59:59+08:00",
        "review-zheng", basis="初查阶段核实取药人与结算人身份",
        timestamp="2026-03-03T09:30:00+08:00")

    r.register_material(
        "M-SETTLE-01", C.SETTLEMENT, "张甲 2024-2026 年结算明细（虚构导出）",
        "市医保平台结算库（虚构）", "2026-03-05T14:00:00+08:00",
        csum("settlement-zhang-jia-2024-2026"),
        "AUTH-01", "lead", "证据室/线索组保管柜", "lead-zhou",
        hypothesis_ids=["H-01"], timestamp="2026-03-05T15:00:00+08:00")
    r.register_material(
        "M-SETTLE-02", C.SETTLEMENT, "王丙 2026-03-05 同日结算记录（虚构导出）",
        "市医保平台结算库（虚构）", "2026-03-05T14:05:00+08:00",
        csum("settlement-wang-bing-20260305"),
        "AUTH-01", "lead", "证据室/线索组保管柜", "lead-zhou",
        hypothesis_ids=["H-01"], timestamp="2026-03-05T15:05:00+08:00")
    r.register_material(
        "M-VIDEO-01", C.SURVEILLANCE, "2026-03-05 门诊取药窗口监控片段（虚构）",
        "某医院安防室（虚构）", "2026-03-06T10:00:00+08:00",
        csum("video-pickup-20260305"),
        "AUTH-01", "surveillance", "证据室/监控组保管柜", "surveillance-wu",
        hypothesis_ids=["H-01"], timestamp="2026-03-06T11:00:00+08:00")

    # 3. 候选对应：监控 ↔ 结算，先提出，不自动合并身份 ------------------
    r.propose_candidate_link(
        "L-01", "M-VIDEO-01", "M-SETTLE-01", "identity",
        "画面取药时间窗与张甲结算时间相差 3 分钟，体貌特征初判一致",
        "surveillance-wu", confidence=0.82,
        timestamp="2026-03-07T09:00:00+08:00")
    r.propose_candidate_link(
        "L-02", "M-VIDEO-01", "M-SETTLE-02", "identity",
        "同日另有王丙结算记录，系统按同药同日推送的候选，需人工排除",
        "surveillance-wu", confidence=0.31,
        timestamp="2026-03-07T09:05:00+08:00")

    # 复核：排除正常患者王丙；确认张甲
    r.review_identity(
        "L-02", LinkStatus.REJECTED, "review-zheng",
        "王丙当日持本人处方在另一窗口取药，衣着与影像不符，排除",
        timestamp="2026-03-08T14:00:00+08:00")
    r.review_identity(
        "L-01", LinkStatus.CONFIRMED, "review-zheng",
        "结合挂号抓拍与取药签字，确认监控取药人即张甲本人",
        timestamp="2026-03-08T14:30:00+08:00")

    # 4. 多类材料+已复核联结，线索才升级 --------------------------------
    r.escalate_lead(
        "H-01", "review-zheng",
        "结算异常与取药监控经复核指向同一人，具备正式调查必要",
        timestamp="2026-03-09T10:00:00+08:00")

    # 第二次取药监控；与结算的对应在初查阶段尚未复核
    r.register_material(
        "M-VIDEO-02", C.SURVEILLANCE, "2026-04-02 取药窗口监控片段（虚构）",
        "某医院安防室（虚构）", "2026-04-03T10:00:00+08:00",
        csum("video-pickup-20260402"),
        "AUTH-01", "surveillance", "证据室/监控组保管柜", "surveillance-wu",
        hypothesis_ids=["H-01"], timestamp="2026-04-03T11:00:00+08:00")
    r.propose_candidate_link(
        "L-03", "M-VIDEO-02", "M-SETTLE-01", "identity",
        "4 月 2 日片段中取药人疑似张甲，待与当月结算明细比对",
        "surveillance-wu", confidence=0.64,
        timestamp="2026-04-04T09:00:00+08:00")

    # 5. 初查阶段并行分析留痕 ------------------------------------------
    r.record_parallel_analysis(
        "A-01", Team.SURVEILLANCE, ["M-VIDEO-01"],
        "取药人面部与步态特征摘录，供复核使用", "surveillance-wu",
        confirms=["L-01"], timestamp="2026-03-10T16:00:00+08:00")
    r.record_parallel_analysis(
        "A-02", Team.LEAD, ["M-SETTLE-01"],
        "张甲 14 个月内开药频次为同类患者 6 倍，剂量超出常规疗程",
        "lead-zhou", timestamp="2026-03-10T16:20:00+08:00")

    # 6. 初查阶段结论：冻结版本，区分已印证与待核 -----------------------
    r.submit_stage_conclusion(
        CaseStage.INITIAL,
        facts=[{
            "fact_id": "F-INIT-1",
            "statement": "2026-03-05 监控取药人与结算记录人张甲为同一人",
            "material_refs": ["M-VIDEO-01", "M-SETTLE-01"],
            "link_refs": ["L-01"],
        }],
        pending=[{
            "item_id": "P-INIT-1",
            "statement": "2026-04-02 取药人是否同为张甲，尚待复核",
            "link_refs": ["L-03"],
        }],
        actor="review-zheng", timestamp="2026-03-12T17:00:00+08:00")

    # 7. 阶段推进后办理补充授权（追溯/资金/物流） -----------------------
    r.grant_authorization(
        "AUTH-02", [C.TRACEABILITY, C.FINANCE, C.LOGISTICS],
        [Team.LEAD, Team.FUND_FLOW, Team.LOGISTICS],
        "2026-03-13T00:00:00+08:00", "2026-08-31T23:59:59+08:00",
        "review-zheng", basis="印证阶段追查药品流向、资金与寄递",
        min_stage=CaseStage.CORROBORATION, supplemental_of="AUTH-01",
        timestamp="2026-03-13T09:00:00+08:00")

    r.register_material(
        "M-TRACE-01", C.TRACEABILITY, "涉案药品追溯码批量流向表（虚构）",
        "药品追溯平台（虚构）", "2026-04-07T10:00:00+08:00",
        csum("trace-codes-batch"),
        "AUTH-02", "lead", "证据室/线索组保管柜", "lead-zhou",
        hypothesis_ids=["H-01"], timestamp="2026-04-07T11:00:00+08:00")
    r.register_material(
        "M-FIN-01", C.FINANCE, "张甲与李乙账户资金往来说明（虚构）",
        "反假协查反馈（虚构）", "2026-04-08T10:00:00+08:00",
        csum("finance-zhang-li-flows"),
        "AUTH-02", "fund_flow", "证据室/资金组保管柜", "fund_flow-chen",
        hypothesis_ids=["H-01"], timestamp="2026-04-08T11:00:00+08:00")
    r.register_material(
        "M-FIN-02", C.FINANCE, "一份来源标注不清的转账截图（虚构）",
        "匿名来信附件（虚构）", "2026-04-08T15:00:00+08:00",
        csum("finance-anonymous-screenshot"),
        "AUTH-02", "fund_flow", "证据室/资金组保管柜", "fund_flow-chen",
        hypothesis_ids=["H-01"], timestamp="2026-04-08T16:00:00+08:00")
    r.register_material(
        "M-LOG-01", C.LOGISTICS, "寄往外地的快递面单及轨迹 7 票（虚构）",
        "寄递企业协查反馈（虚构）", "2026-04-09T10:00:00+08:00",
        csum("logistics-shipments-7"),
        "AUTH-02", "logistics", "证据室/物流组保管柜", "logistics-lin",
        hypothesis_ids=["H-01"], timestamp="2026-04-09T11:00:00+08:00")
    r.register_material(
        "M-LOG-02", C.LOGISTICS, "另一条可疑收件地址的寄递记录（虚构）",
        "寄递企业补充反馈（虚构）", "2026-04-11T10:00:00+08:00",
        csum("logistics-second-address"),
        "AUTH-02", "logistics", "证据室/物流组保管柜", "logistics-lin",
        hypothesis_ids=["H-01"], timestamp="2026-04-11T11:00:00+08:00")

    # 8. 一份材料经查校验/来源问题宣告失效（原记录保留） -----------------
    r.invalidate_material(
        "M-FIN-02", "截图来源无法核验，与银行正式反馈不一致，不得作为证据",
        "review-zheng", timestamp="2026-04-10T09:30:00+08:00")

    # 9. 跨材料联结：追溯码把结算、资金、物流串到同一案件脉络 -----------
    r.propose_candidate_link(
        "L-04", "M-TRACE-01", "M-FIN-01", "event",
        "追溯码显示批量药品转出日与李乙转账日高度重合",
        "fund_flow-chen", confidence=0.77,
        timestamp="2026-04-12T09:00:00+08:00")
    r.propose_candidate_link(
        "L-05", "M-TRACE-01", "M-LOG-01", "event",
        "快递揽收时间与药品追溯码最后扫码地点、时间吻合",
        "logistics-lin", confidence=0.8,
        timestamp="2026-04-12T09:10:00+08:00")
    r.propose_candidate_link(
        "L-06", "M-SETTLE-01", "M-TRACE-01", "event",
        "张甲结算明细中药品批号与追溯表中流向李乙的批号一致",
        "lead-zhou", confidence=0.9,
        timestamp="2026-04-12T09:20:00+08:00")
    r.propose_candidate_link(
        "L-07", "M-LOG-01", "M-LOG-02", "event",
        "两批寄递疑似同一收件网络，尚未取得收件人证言",
        "logistics-lin", confidence=0.55,
        timestamp="2026-04-12T09:30:00+08:00")

    r.review_identity("L-03", LinkStatus.CONFIRMED, "review-zheng",
                      "4 月 2 日签字与挂号抓拍再次指向张甲本人",
                      timestamp="2026-04-13T10:00:00+08:00")
    r.review_identity("L-04", LinkStatus.CONFIRMED, "review-zheng",
                      "银行流水备注与金额能够对应药品批次数量",
                      timestamp="2026-04-13T10:30:00+08:00")
    r.review_identity("L-05", LinkStatus.CONFIRMED, "review-zheng",
                      "面单手机号与揽收影像和追溯扫码记录互证",
                      timestamp="2026-04-13T11:00:00+08:00")
    r.review_identity("L-06", LinkStatus.CONFIRMED, "review-zheng",
                      "批号重合率 92%，排除同号偶合",
                      timestamp="2026-04-13T11:30:00+08:00")
    # L-07 保持 candidate：印证阶段结束时仍为待核关联

    # 10. 跨部门（小组）限时借阅：监控组借阅追溯材料核画面 --------------
    r.grant_loan(
        "LOAN-01", "M-TRACE-01", Team.SURVEILLANCE,
        "核对另一段监控中出现的药盒批号，阅毕归还",
        "2026-04-14T09:00:00+08:00", "2026-04-21T18:00:00+08:00",
        "review-zheng", timestamp="2026-04-14T08:30:00+08:00")

    # 11. 印证阶段并行分析 ---------------------------------------------
    r.record_parallel_analysis(
        "A-03", Team.FUND_FLOW, ["M-FIN-01", "M-TRACE-01"],
        "12 笔转账按药品批次计价，金额与转售行情吻合",
        "fund_flow-chen", confirms=["L-04"],
        timestamp="2026-04-15T15:00:00+08:00")
    r.record_parallel_analysis(
        "A-04", Team.LOGISTICS, ["M-LOG-01", "M-TRACE-01"],
        "7 票寄递的寄件署名与张甲通信号关联，收件地集中",
        "logistics-lin", confirms=["L-05"],
        timestamp="2026-04-15T15:30:00+08:00")

    # 12. 印证阶段结论：冻结全部所用材料版本 ----------------------------
    r.submit_stage_conclusion(
        CaseStage.CORROBORATION,
        facts=[
            {
                "fact_id": "F-CORR-1",
                "statement": "张甲两次取药（2026-03-05、2026-04-02）均经复核确认",
                "material_refs": ["M-VIDEO-01", "M-VIDEO-02", "M-SETTLE-01"],
                "link_refs": ["L-01", "L-03"],
            },
            {
                "fact_id": "F-CORR-2",
                "statement": "张甲名下药品经追溯码流向李乙，并伴随对应资金与寄递，相互印证",
                "material_refs": ["M-SETTLE-01", "M-TRACE-01",
                                  "M-FIN-01", "M-LOG-01"],
                "link_refs": ["L-04", "L-05", "L-06"],
            },
        ],
        pending=[{
            "item_id": "P-CORR-1",
            "statement": "第二收件地址是否属于同一收药网络，待补充证言后复核",
            "link_refs": ["L-07"],
        }],
        actor="review-zheng", timestamp="2026-04-20T17:00:00+08:00")

    # 13. 冻结后移交：日志保留每份材料事后去向 --------------------------
    r.transfer_material(
        "M-FIN-01", "负责人复核档案柜", "review-zheng",
        "随印证阶段结论移送复核", timestamp="2026-04-22T09:00:00+08:00")
    r.transfer_material(
        "M-LOG-01", "负责人复核档案柜", "review-zheng",
        "随印证阶段结论移送复核", timestamp="2026-04-22T09:10:00+08:00")

    return r


def main() -> None:
    room = build()
    out = Path(__file__).resolve().parent / "case.json"
    out.write_text(json.dumps(room.to_dict(), ensure_ascii=False, indent=2),
                   encoding="utf-8")
    print(f"wrote {out} with {len(room.events)} events")


if __name__ == "__main__":
    main()
