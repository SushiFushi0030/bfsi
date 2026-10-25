"""Public artifacts must remain compatible with the frozen contracts."""
import json
from pathlib import Path
import unittest

from jsonschema import Draft202012Validator
from bfsi.config import RESOURCES

ROOT = Path(__file__).resolve().parents[1]


class PublicContractTests(unittest.TestCase):
    def test_documented_schemas_match_packaged_contracts(self):
        schemas = sorted((ROOT / "schemas").glob("*.json"))
        self.assertEqual(len(schemas), 3)
        for schema in schemas:
            with self.subTest(schema=schema.name):
                self.assertEqual(schema.read_bytes(), (RESOURCES / schema.name).read_bytes())
                Draft202012Validator.check_schema(json.loads(schema.read_text()))

    def test_invented_model_labels_follow_frozen_schema(self):
        schema = json.loads((RESOURCES / "simplified_semantic_schema_v0.1.json").read_text())
        validator = Draft202012Validator(schema)
        dataset = json.loads((ROOT / "examples/demo/semantic.json").read_text())
        self.assertTrue(dataset["synthetic"])
        for row in dataset["records"]:
            validator.validate({key: row[key] for key in schema["required"]})


if __name__ == "__main__":
    unittest.main()
