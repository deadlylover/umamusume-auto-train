import unittest
from unittest.mock import patch

import core.skeleton as skeleton
import scenarios.trackblazer as trackblazer


def _entry(key, passed=False, score=0.0):
    return {
        "key": key,
        "matched": True,
        "passed_threshold": bool(passed),
        "score": float(score),
        "click_target": (100, 200),
        "location": [10, 20],
        "size": [30, 40],
        "region_ltrb": [1, 2, 3, 4],
        "template": key,
    }


class TrackblazerShopRecoveryChecks(unittest.TestCase):
    def test_inventory_detection_rejects_shop_screen_overlap(self):
        with patch.object(trackblazer, "_trackblazer_ui_region", return_value=(1, 2, 3, 4)), patch.object(
            trackblazer.device_action,
            "screenshot",
            return_value=object(),
        ), patch.object(
            trackblazer,
            "_shop_confirm_template_keys",
            return_value=("shop_confirm",),
        ), patch.dict(
            trackblazer.constants.TRACKBLAZER_ITEM_USE_TEMPLATES,
            {
                "use_training_items": "use_training_items_tpl",
                "use_back": "use_back_tpl",
            },
            clear=False,
        ), patch.dict(
            trackblazer.constants.TRACKBLAZER_SHOP_UI_TEMPLATES,
            {
                "shop_confirm": "shop_confirm_tpl",
                "shop_aftersale_close": "shop_aftersale_close_tpl",
            },
            clear=False,
        ), patch.object(
            trackblazer,
            "detect_inventory_controls",
            return_value={
                "confirm_candidates": {
                    "inventory_confirm_use_available": _entry("inventory_confirm_use_available", passed=False, score=0.73),
                    "inventory_confirm_use_unavailable": _entry("inventory_confirm_use_unavailable", passed=False, score=0.71),
                }
            },
        ), patch.object(
            trackblazer,
            "_best_match_entry",
            side_effect=lambda template_path, **_: {
                "use_training_items_tpl": _entry("use_training_items", passed=False, score=0.43),
                "use_back_tpl": _entry("use_back", passed=True, score=1.0),
                "shop_confirm_tpl": _entry("shop_confirm", passed=True, score=0.9999),
                "shop_aftersale_close_tpl": _entry("shop_aftersale_close", passed=False, score=0.56),
            }[template_path],
        ):
            opened, entry, checks = trackblazer.detect_inventory_screen(threshold=0.75)

        self.assertFalse(opened)
        self.assertIsNone(entry)
        self.assertTrue(any(item.get("key") == "use_back" and item.get("passed_threshold") for item in checks))
        self.assertTrue(any(item.get("key") == "shop_confirm" and item.get("passed_threshold") for item in checks))

    def test_shop_detection_rejects_inventory_specific_controls(self):
        with patch.object(trackblazer, "_trackblazer_ui_region", return_value=(1, 2, 3, 4)), patch.object(
            trackblazer.device_action,
            "screenshot",
            return_value=object(),
        ), patch.object(
            trackblazer,
            "_shop_confirm_template_keys",
            return_value=("shop_confirm",),
        ), patch.dict(
            trackblazer.constants.TRACKBLAZER_ITEM_USE_TEMPLATES,
            {
                "use_training_items": "use_training_items_tpl",
                "use_back": "use_back_tpl",
            },
            clear=False,
        ), patch.dict(
            trackblazer.constants.TRACKBLAZER_SHOP_UI_TEMPLATES,
            {
                "shop_confirm": "shop_confirm_tpl",
                "shop_aftersale_close": "shop_aftersale_close_tpl",
            },
            clear=False,
        ), patch.object(
            trackblazer,
            "detect_inventory_controls",
            return_value={
                "confirm_candidates": {
                    "inventory_confirm_use_available": _entry("inventory_confirm_use_available", passed=True, score=0.997),
                    "inventory_confirm_use_unavailable": _entry("inventory_confirm_use_unavailable", passed=False, score=0.21),
                }
            },
        ), patch.object(
            trackblazer,
            "_best_match_entry",
            side_effect=lambda template_path, **_: {
                "use_training_items_tpl": _entry("use_training_items", passed=True, score=0.998),
                "use_back_tpl": _entry("use_back", passed=True, score=1.0),
                "shop_confirm_tpl": _entry("shop_confirm", passed=False, score=0.41),
                "shop_aftersale_close_tpl": _entry("shop_aftersale_close", passed=False, score=0.22),
            }[template_path],
        ):
            opened, entry, checks = trackblazer.detect_shop_screen(threshold=0.75)

        self.assertFalse(opened)
        self.assertIsNone(entry)
        self.assertTrue(any(item.get("key") == "use_training_items" and item.get("passed_threshold") for item in checks))

    def test_shop_entry_verification_failure_blocks_execute_flow(self):
        flow = {
            "entered": False,
            "closed": False,
            "reason": "shop_verification_failed",
            "entry_result": {
                "clicked": True,
                "entered": False,
            },
        }
        result_payload = {
            "success": False,
            "trackblazer_shop_flow": flow,
            "trackblazer_shop_items": [],
            "trackblazer_shop_summary": {},
        }
        state_obj = {}

        with patch.object(skeleton, "_attach_trackblazer_pre_action_item_plan"), patch.object(
            skeleton,
            "_trackblazer_shop_buy_plan",
            return_value=[{"key": "royal_kale_juice"}],
        ), patch.object(
            skeleton,
            "_invalidate_trackblazer_inventory_cache",
        ), patch(
            "scenarios.trackblazer.execute_trackblazer_shop_purchases",
            return_value=result_payload,
        ):
            run_result = skeleton._run_trackblazer_shop_purchases(state_obj, action=object())

        self.assertEqual(run_result.get("status"), "blocked")
        self.assertEqual(run_result.get("reason"), "shop_verification_failed")
        self.assertEqual(state_obj.get("trackblazer_shop_flow"), flow)


if __name__ == "__main__":
    unittest.main()
