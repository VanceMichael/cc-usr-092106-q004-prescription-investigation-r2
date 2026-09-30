"""只增不改的决定日志。

每条事件携带递增序号、时间戳、决定人、负载与前一条事件的哈希，
形成哈希链；任何对既有事件的覆盖、删除或重排都会让链校验失败。
"""

import hashlib
import json
from datetime import datetime, timezone
from typing import Any


def canonical(payload: Any) -> bytes:
    """负载的规范化字节，用于哈希计算（键排序、无多余空白）。"""
    return json.dumps(payload, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")


def digest(seq: int, timestamp: str, actor: str, kind: str,
           payload: Any, prev_hash: str) -> str:
    body = b"\x1f".join([
        str(seq).encode("ascii"),
        timestamp.encode("utf-8"),
        actor.encode("utf-8"),
        kind.encode("utf-8"),
        canonical(payload),
        prev_hash.encode("ascii"),
    ])
    return hashlib.sha256(body).hexdigest()


def append_event(events: list[dict], kind: str, payload: dict,
                 actor: str, timestamp: str | None = None) -> dict:
    """向日志追加一条事件，返回该事件。

    调用方拿到的是同一列表引用；本函数不提供修改或删除入口，
    以保证"决定不可覆盖"。
    """
    seq = len(events)
    ts = timestamp or datetime.now(timezone.utc).isoformat()
    prev_hash = events[-1]["hash"] if events else "GENESIS"
    event = {
        "seq": seq,
        "timestamp": ts,
        "actor": actor,
        "kind": kind,
        "payload": payload,
        "prev_hash": prev_hash,
        "hash": digest(seq, ts, actor, kind, payload, prev_hash),
    }
    events.append(event)
    return event


def verify_chain(events: list[dict]) -> None:
    """校验整条日志：序号连续、哈希首尾相接。失败时抛出 ValueError。"""
    prev_hash = "GENESIS"
    for i, event in enumerate(events):
        if event["seq"] != i:
            raise ValueError(f"事件序号不连续：位置 {i} 记录为 {event['seq']}")
        expected = digest(
            event["seq"], event["timestamp"], event["actor"],
            event["kind"], event["payload"], event["prev_hash"],
        )
        if event["prev_hash"] != prev_hash:
            raise ValueError(f"事件 {i} 前序哈希断裂")
        if event["hash"] != expected:
            raise ValueError(f"事件 {i} 哈希不匹配：内容可能被覆盖")
        prev_hash = event["hash"]
