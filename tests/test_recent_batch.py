"""Verify catalog identity/coverage guards without downloads or model inference."""

import copy
import datetime as dt
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bilibili_recent_batch as batch
from bilibili_repair_repeats import find_loops


class CatalogValidationTests(unittest.TestCase):
    def setUp(self):
        self.row = {"bvid": "BVexample", "date": "2026-11-11", "duration": "01:00"}
        self.info = {
            "id": "BVexample", "uploader_id": "1962124785", "duration": 60,
            "timestamp": dt.datetime(2026, 11, 11, 10, 30, tzinfo=batch.TZ).timestamp(),
            "formats": [{"acodec": "aac", "vcodec": "none", "url": "https://example.com/audio"}],
        }

    def validate(self, **changes):
        info = copy.deepcopy(self.info)
        info.update(changes)
        return batch.validate_info(info, self.row, "1962124785", "2026-11-08", "2026-11-11")

    def test_verified_catalog_matches(self):
        self.assertEqual(self.validate(), ("2026-11-11", 60))

    def test_wrong_owner_or_video_is_rejected(self):
        for change in [{"uploader_id": "other"}, {"id": "BVother"}]:
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.validate(**change)

    def test_wrong_day_or_missing_timestamp_is_rejected(self):
        for timestamp in [None, self.info["timestamp"] - 86400 * 10]:
            with self.subTest(timestamp=timestamp), self.assertRaises(ValueError):
                self.validate(timestamp=timestamp)

    def test_partial_duration_or_multi_part_is_rejected(self):
        for change in [{"duration": 15}, {"_type": "multi_video"}, {"formats": []}]:
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.validate(**change)


class RepeatedAsrTests(unittest.TestCase):
    def test_repeated_loop_preserves_short_or_empty_runs(self):
        self.assertEqual(find_loops([]), [])
        segments = [{"text": t} for t in ["引言", "重复", "重复", "重复", "重复", "结尾", "结尾", "结尾"]]
        self.assertEqual(find_loops(segments), [(1, 4)])


if __name__ == "__main__":
    unittest.main()
