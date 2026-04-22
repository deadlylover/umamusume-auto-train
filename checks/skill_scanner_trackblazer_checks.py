import unittest
from unittest.mock import patch

import numpy as np

import core.skill_scanner as skill_scanner
import utils.constants as constants


def _base_row(**overrides):
    row = {
        "text_raw": "Corner Recovery",
        "text_normalized": skill_scanner._normalize_skill_text("Corner Recovery"),
        "confidence": 0.91,
        "abs_y_center": float(constants.SCROLLING_SKILL_SCREEN_BBOX[1] + 120),
        "crop_bbox": [48, 100, 140, 24],
        "ocr_variant": "normal",
        "match_name": "Corner Recovery",
        "name_match_method": "exact",
        "obtained": False,
        "obtained_evidence": "none",
        "increment_match": None,
        "increment_pairing": None,
        "increment_vertical_distance": None,
        "increment_title_offset": None,
    }
    row.update(overrides)
    return row


class SkillScannerTrackblazerChecks(unittest.TestCase):
    def setUp(self):
        skill_scanner._SKILL_MATCH_CACHE.clear()
        skill_scanner._SKILL_SHORTLIST_CACHE.clear()

    def test_grey_row_with_obtained_badge_is_non_actionable(self):
        row = _base_row(ocr_variant="dim")
        screenshot = np.zeros((8, 8, 3), dtype=np.uint8)
        scroll_top = int(constants.SCROLLING_SKILL_SCREEN_BBOX[1])
        badge = (20, int(row["abs_y_center"] - scroll_top + 42 - 10), 30, 20)
        with patch.object(skill_scanner, "_detect_obtained_badges", return_value=[badge]), patch.object(
            skill_scanner,
            "_detect_obtained_text_tokens",
            return_value=[],
        ):
            reason = skill_scanner._classify_no_increment_row(row, screenshot)
        self.assertEqual(reason, "target_obtained_no_increment")
        self.assertTrue(row.get("obtained"))
        self.assertEqual(row.get("obtained_evidence"), "template")

    def test_grey_row_with_obtained_text_is_non_actionable(self):
        row = _base_row(ocr_variant="dim")
        screenshot = np.zeros((8, 8, 3), dtype=np.uint8)
        obtained_text = {
            "text_raw": "Obtained",
            "text_normalized": "obtained",
            "abs_y_center": row["abs_y_center"] + 42.0,
            "score": 0.93,
            "confidence": 0.88,
            "crop_bbox": [0, 0, 10, 10],
        }
        with patch.object(skill_scanner, "_detect_obtained_badges", return_value=[]), patch.object(
            skill_scanner,
            "_detect_obtained_text_tokens",
            return_value=[obtained_text],
        ):
            reason = skill_scanner._classify_no_increment_row(row, screenshot)
        self.assertEqual(reason, "target_obtained_no_increment")
        self.assertTrue(row.get("obtained"))
        self.assertEqual(row.get("obtained_evidence"), "text")

    def test_buyable_row_with_increment_stays_actionable(self):
        row = _base_row(
            increment_match=[10, 20, 18, 18],
            increment_pairing="vertical",
            increment_vertical_distance=42.0,
            increment_title_offset=42.0,
        )
        self.assertTrue(skill_scanner._match_has_safe_increment(row))
        entry = skill_scanner._build_scan_entry("Corner Recovery", source="check")
        skill_scanner._sync_target_entry_telemetry(entry, match_row=row)
        self.assertTrue(entry["increment_present"])
        self.assertEqual(entry["obtained_evidence"], "none")
        self.assertEqual(entry["name_match_method"], "exact")

    def test_bright_normal_row_preferred_over_dim_variant(self):
        shortlist = ["Corner Recovery"]
        rows = [
            _base_row(
                confidence=0.95,
                ocr_variant="normal",
                text_raw="Corner Recovery",
                text_normalized=skill_scanner._normalize_skill_text("Corner Recovery"),
            ),
            _base_row(
                confidence=0.62,
                ocr_variant="dim",
                text_raw="Comer Recovery",
                text_normalized=skill_scanner._normalize_skill_text("Comer Recovery"),
                crop_bbox=[52, 103, 138, 24],
            ),
        ]
        matched = skill_scanner._match_skill_rows_to_shortlist(rows, shortlist)
        self.assertEqual(len(matched), 1)
        self.assertEqual(matched[0]["match_name"], "Corner Recovery")
        self.assertEqual(matched[0]["ocr_variant"], "normal")
        self.assertEqual(matched[0]["name_match_method"], "exact")

    def test_token_overlap_fallback_scores_split_like_row(self):
        shortlist = ["Corner Recovery"]
        row = _base_row(
            text_raw="Corner Elite Recovery",
            text_normalized=skill_scanner._normalize_skill_text("Corner Elite Recovery"),
            confidence=0.66,
            ocr_variant="dim",
        )
        matched = skill_scanner._match_skill_rows_to_shortlist([row], shortlist)
        self.assertEqual(len(matched), 1)
        self.assertEqual(matched[0]["match_name"], "Corner Recovery")
        self.assertEqual(matched[0]["name_match_method"], "token_merge")

    def test_dim_only_corner_adept_hallucination_is_rejected_when_normal_disagrees(self):
        shortlist = ["Corner Adept ○"]
        rows = [
            _base_row(
                confidence=0.96,
                ocr_variant="normal",
                text_raw="Long Corners",
                text_normalized=skill_scanner._normalize_skill_text("Long Corners"),
            ),
            _base_row(
                confidence=0.74,
                ocr_variant="dim",
                text_raw="Corner Adept ○",
                text_normalized=skill_scanner._normalize_skill_text("Corner Adept ○"),
                crop_bbox=[50, 102, 144, 24],
            ),
        ]
        matched = skill_scanner._match_skill_rows_to_shortlist(rows, shortlist)
        self.assertEqual(matched, [])

    def test_true_corner_adept_dim_row_survives_when_normal_variant_corroborates(self):
        shortlist = ["Corner Adept ○"]
        rows = [
            _base_row(
                confidence=0.67,
                ocr_variant="normal",
                text_raw="Comer Adept ○",
                text_normalized=skill_scanner._normalize_skill_text("Comer Adept ○"),
            ),
            _base_row(
                confidence=0.74,
                ocr_variant="dim",
                text_raw="Corner Adept ○",
                text_normalized=skill_scanner._normalize_skill_text("Corner Adept ○"),
                crop_bbox=[50, 102, 144, 24],
            ),
        ]
        matched = skill_scanner._match_skill_rows_to_shortlist(rows, shortlist)
        self.assertEqual(len(matched), 1)
        self.assertEqual(matched[0]["match_name"], "Corner Adept ○")
        self.assertEqual(matched[0]["chosen_variant"], "dim")
        self.assertEqual(matched[0]["ocr_variants_seen"], ["normal", "dim"])
        self.assertIn("adept", matched[0]["token_evidence"]["distinctive_tokens_present"])
        self.assertGreaterEqual(float(matched[0]["consensus_score"]), 0.92)

    def test_description_line_is_not_paired_to_nearby_increment(self):
        row = _base_row(
            text_raw="Taking the Lead",
            text_normalized=skill_scanner._normalize_skill_text("Taking the Lead"),
            match_name="Taking the Lead",
        )
        scroll_top = int(constants.SCROLLING_SKILL_SCREEN_BBOX[1])
        row_rel_y = int(round(row["abs_y_center"] - scroll_top))
        increment = [600, row_rel_y - 9, 18, 18]
        self.assertIsNone(skill_scanner._pair_skill_row_to_increment(row, [increment]))

    def test_title_line_pairs_to_lower_increment(self):
        row = _base_row(
            text_raw="Taking the Lead",
            text_normalized=skill_scanner._normalize_skill_text("Taking the Lead"),
            match_name="Taking the Lead",
        )
        scroll_top = int(constants.SCROLLING_SKILL_SCREEN_BBOX[1])
        row_rel_y = int(round(row["abs_y_center"] - scroll_top))
        increment = [600, row_rel_y + 42 - 9, 18, 18]
        self.assertEqual(skill_scanner._pair_skill_row_to_increment(row, [increment]), increment)

    def test_safe_title_candidate_wins_over_exact_description_candidate(self):
        scroll_top = int(constants.SCROLLING_SKILL_SCREEN_BBOX[1])
        title_row = _base_row(
            text_raw="Taklng the Lead",
            text_normalized=skill_scanner._normalize_skill_text("Taklng the Lead"),
            confidence=0.72,
            abs_y_center=float(scroll_top + 120),
            crop_bbox=[48, 110, 150, 24],
        )
        description_row = _base_row(
            text_raw="Taking the Lead",
            text_normalized=skill_scanner._normalize_skill_text("Taking the Lead"),
            confidence=0.97,
            abs_y_center=float(scroll_top + 170),
            crop_bbox=[48, 160, 150, 24],
        )
        matched = skill_scanner._match_skill_rows_to_shortlist(
            [title_row, description_row],
            ["Taking the Lead"],
        )
        self.assertEqual(len(matched), 2)

        increment = [600, int(title_row["abs_y_center"] - scroll_top + 42 - 9), 18, 18]
        for row in matched:
            inc = skill_scanner._pair_skill_row_to_increment(row, [increment])
            if inc:
                row["increment_match"] = list(inc)
                row["increment_pairing"] = "vertical"
                row["increment_vertical_distance"] = skill_scanner._increment_match_vertical_distance(row, inc)
                row["increment_title_offset"] = skill_scanner._increment_match_title_offset(row, inc)

        best = skill_scanner._find_best_target_match(
            {"matched_targets": matched},
            "Taking the Lead",
        )
        self.assertTrue(skill_scanner._match_has_safe_increment(best))
        self.assertEqual(best["text_raw"], "Taklng the Lead")

    def test_second_wind_restart_lead_descriptions_do_not_match_taking_the_lead(self):
        rows = [
            _base_row(
                text_raw="not in the lead mid-race. (Front",
                text_normalized=skill_scanner._normalize_skill_text("not in the lead mid-race. (Front"),
                confidence=1.0,
            ),
            _base_row(
                text_raw="ahead when not in the lead",
                text_normalized=skill_scanner._normalize_skill_text("ahead when not in the lead"),
                confidence=1.0,
                abs_y_center=_base_row()["abs_y_center"] + 48,
            ),
        ]
        matched = skill_scanner._match_skill_rows_to_shortlist(rows, ["Taking the Lead"])
        self.assertEqual(matched, [])

    def test_reversed_lead_description_does_not_match_early_lead(self):
        row = _base_row(
            text_raw="not in the lead early-race. (Front Runner)",
            text_normalized=skill_scanner._normalize_skill_text("not in the lead early-race. (Front Runner)"),
            confidence=1.0,
        )
        matched = skill_scanner._match_skill_rows_to_shortlist([row], ["Early Lead"])
        self.assertEqual(matched, [])


if __name__ == "__main__":
    unittest.main()
