"""Cover accidental writes to verified tables shared across sequential edits."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import pandas as pd

from chinatravel.personalization.edit_runtime import decoded_intercity_tables


class EditRuntimeTests(unittest.TestCase):
    def test_readers_get_independent_copies_and_unrelated_files_remain_uncached(self):
        with TemporaryDirectory() as folder:
            database = Path(folder)
            train = database / "intercity_transport" / "train" / "from_first_to_second.json"
            train.parent.mkdir(parents=True)
            train.write_text(json.dumps([{"price": 25, "stops": [{"name": "second"}]}]), encoding="utf-8")
            other = database / "other.json"
            other.write_text('[{"price": 12}]', encoding="utf-8")
            original_read = pd.read_json

            with decoded_intercity_tables(database):
                first = pd.read_json(train)
                first.at[0, "price"] = 1
                first.at[0, "stops"][0]["name"] = "changed"
                again = pd.read_json(train)
                self.assertEqual(again.at[0, "price"], 25)
                self.assertEqual(again.at[0, "stops"], [{"name": "second"}])
                self.assertEqual(pd.read_json(other).at[0, "price"], 12)
                other.write_text('[{"price": 8}]', encoding="utf-8")
                self.assertEqual(pd.read_json(other).at[0, "price"], 8)

            self.assertIs(pd.read_json, original_read)
