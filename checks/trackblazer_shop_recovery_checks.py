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
    def test_enter_shop_retries_verification_after_click(self):
        shop_state = {
            "best_method": {
                "method": "lobby_button",
                "matched": True,
                "entry": {
                    "click_target": (320, 1280),
                },
            }
        }

        with patch.object(
            trackblazer,
            "inspect_shop_entry_state",
            return_value=shop_state,
        ), patch.object(
            trackblazer.device_action,
            "click_with_metrics",
            return_value={"clicked": True},
        ), patch.object(
            trackblazer,
            "detect_shop_screen",
            side_effect=[
                (False, None, []),
                (True, _entry("shop_confirm", passed=True, score=0.99), [_entry("shop_confirm", passed=True, score=0.99)]),
            ],
        ), patch.object(
            trackblazer,
            "sleep",
            return_value=None,
        ):
            result = trackblazer.enter_shop(threshold=0.8, read_shop_coins=False)

        self.assertTrue(result.get("clicked"))
        self.assertTrue(result.get("entered"))
        self.assertEqual(result.get("reason"), "clicked_lobby_button_shop")
        self.assertEqual(len(result.get("verification_attempts") or []), 2)

    def test_execute_shop_purchase_failure_preserves_live_scan_state(self):
        shared_scan = {
            "all_items": ["stamina_manual"],
            "purchasable_items": [],
            "pages": [{"rows": [], "visible_items": ["stamina_manual"]}],
            "flow": {"stop_reason": "bottom_reached", "timing": {}},
        }

        with patch.object(
            trackblazer,
            "enter_shop",
            return_value={"entered": True, "shop_coins": 18},
        ), patch.object(
            trackblazer,
            "close_trackblazer_shop",
            return_value={"closed": True},
        ), patch.object(
            trackblazer,
            "get_shop_catalog",
            return_value=[{"key": "stamina_manual", "display_name": "Stamina Manual"}],
        ):
            result = trackblazer.execute_trackblazer_shop_purchases(
                ["stamina_manual"],
                cached_shop_scan=shared_scan,
            )

        self.assertFalse(result.get("success"))
        self.assertEqual(result.get("trackblazer_shop_items"), [])
        self.assertEqual(
            result.get("trackblazer_shop_summary"),
            {
                "items_detected": ["stamina_manual"],
                "purchasable_items": [],
                "page_count": 1,
                "stop_reason": "bottom_reached",
                "shop_coins": 18,
            },
        )
        self.assertEqual(
            (result.get("trackblazer_shop_flow") or {}).get("reason"),
            "shop_items_not_purchasable_in_scan",
        )

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

    def test_shop_detection_accepts_shop_controls_region_marker(self):
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
                "shop_aftersale_confirm_use_available": "shop_aftersale_confirm_use_available_tpl",
                "shop_aftersale_confirm_use_unavailable": "shop_aftersale_confirm_use_unavailable_tpl",
            },
            clear=False,
        ), patch.object(
            trackblazer,
            "detect_inventory_controls",
            return_value={
                "close": _entry("close", passed=False, score=0.32),
                "confirm_use": _entry("shop_aftersale_confirm_use_available", passed=True, score=0.996),
                "confirm_candidates": {
                    "shop_confirm": _entry("shop_confirm", passed=False, score=0.41),
                    "shop_aftersale_confirm_use_available": _entry("shop_aftersale_confirm_use_available", passed=True, score=0.996),
                    "shop_aftersale_confirm_use_unavailable": _entry("shop_aftersale_confirm_use_unavailable", passed=False, score=0.27),
                    "inventory_confirm_use_available": _entry("inventory_confirm_use_available", passed=False, score=0.18),
                    "inventory_confirm_use_unavailable": _entry("inventory_confirm_use_unavailable", passed=False, score=0.16),
                },
            },
        ), patch.object(
            trackblazer,
            "_best_match_entry",
            side_effect=lambda template_path, **_: {
                "use_training_items_tpl": _entry("use_training_items", passed=False, score=0.33),
                "use_back_tpl": _entry("use_back", passed=False, score=0.29),
                "shop_confirm_tpl": _entry("shop_confirm", passed=False, score=0.41),
                "shop_aftersale_close_tpl": _entry("shop_aftersale_close", passed=False, score=0.35),
            }[template_path],
        ):
            opened, entry, checks = trackblazer.detect_shop_screen(threshold=0.75)

        self.assertTrue(opened)
        self.assertEqual((entry or {}).get("key"), "shop_aftersale_confirm_use_available")
        self.assertTrue(any(item.get("key") == "shop_aftersale_confirm_use_available" and item.get("passed_threshold") for item in checks))

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

    def test_lobby_scan_recovers_leftover_shop_overlay_when_anchors_are_zero(self):
        with patch.object(skeleton.constants, "SCENARIO_NAME", "trackblazer"), patch.object(
            skeleton.device_action,
            "flush_screenshot_cache",
        ), patch(
            "scenarios.trackblazer.detect_shop_screen",
            return_value=(True, _entry("shop_confirm", passed=True, score=0.99), []),
        ), patch(
            "scenarios.trackblazer.close_trackblazer_shop",
            return_value={"closed": True},
        ) as close_shop_mock, patch(
            "scenarios.trackblazer.detect_inventory_screen",
            return_value=(False, None, []),
        ):
            result = skeleton._recover_trackblazer_overlay_from_lobby_scan(
                {"training_button": 0, "rest_button": 0, "details_button": 0}
            )

        self.assertTrue(result.get("attempted"))
        self.assertEqual(result.get("overlay"), "shop")
        self.assertTrue(result.get("closed"))
        close_shop_mock.assert_called_once()

    def test_lobby_scan_does_not_attempt_overlay_recovery_when_anchors_exist(self):
        with patch.object(skeleton.constants, "SCENARIO_NAME", "trackblazer"), patch(
            "scenarios.trackblazer.detect_shop_screen",
        ) as detect_shop_mock, patch(
            "scenarios.trackblazer.detect_inventory_screen",
        ) as detect_inventory_mock:
            result = skeleton._recover_trackblazer_overlay_from_lobby_scan(
                {"training_button": 1, "rest_button": 0, "details_button": 0}
            )

        self.assertFalse(result.get("attempted"))
        detect_shop_mock.assert_not_called()
        detect_inventory_mock.assert_not_called()

    def test_run_action_refreshes_planner_payload_after_shop_failure(self):
        state_obj = {"trackblazer_shop_items": [], "trackblazer_shop_summary": {"purchasable_items": []}}
        action = type("ActionStub", (), {})()
        action.func = "do_training"
        action.run = lambda: "executed"

        with patch.object(skeleton, "review_action_before_execution", return_value=True), patch.object(
            skeleton,
            "_wait_for_execute_intent",
            return_value="execute",
        ), patch.object(
            skeleton,
            "_run_skill_purchase_plan",
            return_value={"status": "skipped"},
        ), patch.object(
            skeleton,
            "_run_trackblazer_shop_purchases",
            return_value={
                "status": "failed",
                "reason": "shop_items_not_purchasable_in_scan",
                "result": {"trackblazer_shop_flow": {"entered": True, "closed": True}},
            },
        ), patch.object(
            skeleton,
            "_attach_trackblazer_pre_action_item_plan",
        ) as attach_mock, patch.object(
            skeleton,
            "_ocr_debug_for_action",
            return_value=["fresh-debug"],
        ), patch.object(
            skeleton,
            "_planned_clicks_for_action",
            return_value=["fresh-clicks"],
        ), patch.object(
            skeleton,
            "_run_trackblazer_pre_action_items",
            return_value={"status": "skipped"},
        ), patch.object(
            skeleton,
            "_enforce_operator_race_gate_before_execute",
            return_value="ok",
        ), patch.object(
            skeleton,
            "_run_planner_race_preflight",
            return_value="continue",
        ), patch.object(
            skeleton,
            "_resolve_post_action_resolution",
            return_value="executed",
        ), patch.object(
            skeleton,
            "update_operator_snapshot",
        ):
            result = skeleton.run_action_with_review(
                state_obj,
                action,
                review_message="test",
                ocr_debug=["stale-debug"],
                planned_clicks=["stale-clicks"],
            )

        self.assertEqual(result, "executed")
        attach_mock.assert_called_once_with(state_obj, action)


if __name__ == "__main__":
    unittest.main()
