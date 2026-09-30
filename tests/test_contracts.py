"""校验 fixtures 与 contracts 中的 JSON 契约一致。"""

import json
import unittest
from pathlib import Path

import jsonschema

ROOT = Path(__file__).resolve().parent.parent


def _load(rel: str) -> dict:
    return json.loads((ROOT / rel).read_text(encoding="utf-8"))


class ContractTest(unittest.TestCase):
    def test_context_fixture_matches_contract(self):
        jsonschema.validate(_load("fixtures/context.json"),
                            _load("contracts/context.schema.json"))

    def test_case_fixture_matches_contract(self):
        jsonschema.validate(_load("fixtures/case.json"),
                            _load("contracts/evidence-room.schema.json"))


if __name__ == "__main__":
    unittest.main()
