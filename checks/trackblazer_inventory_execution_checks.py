import unittest
from unittest.mock import patch

import scenarios.trackblazer as trackblazer


class TrackblazerInventoryExecutionChecks(unittest.TestCase):
    def test_infer_inventory_execution_scroll_page_falls_back_to_scan_history(self):
        self.assertEqual(
            trackblazer._infer_inventory_execution_scroll_page(
                scan_timing={"scroll": {"pages_scanned": 2}},
                scrollbar_state={},
            ),
            "bottom",
        )
        self.assertEqual(
            trackblazer._infer_inventory_execution_scroll_page(
                scan_timing={"scroll": {"pages_scanned": 1}},
                scrollbar_state={},
            ),
            "top",
        )

    def test_order_inventory_execution_items_prefers_current_page(self):
        inventory = {
            "top_item": {"scroll_page": "top"},
            "shared_item": {"scroll_page": "both"},
            "bottom_item": {"scroll_page": "bottom"},
        }
        ordered = trackblazer._order_inventory_execution_items(
            ["top_item", "shared_item", "bottom_item"],
            inventory,
            current_scroll="bottom",
        )
        self.assertEqual(ordered, ["shared_item", "bottom_item", "top_item"])

    def test_execute_training_items_reorders_bottom_page_first_in_dry_run(self):
        scanned_inventory = {
            "top_item": {
                "detected": True,
                "increment_target": None,
                "increment_match": None,
                "row_center_y": 120,
                "held_quantity": 1,
                "scroll_page": "top",
                "increment_target_stale": True,
            },
            "bottom_item": {
                "detected": True,
                "increment_target": (510, 920),
                "increment_match": [10, 20, 30, 40],
                "row_center_y": 780,
                "held_quantity": 1,
                "scroll_page": "bottom",
            },
            "_timing": {
                "scroll": {
                    "pages_scanned": 2,
                }
            },
        }
        rescanned_top_page = {
            "top_item": {
                "detected": True,
                "increment_target": (210, 320),
                "increment_match": [11, 21, 31, 41],
                "row_center_y": 140,
                "held_quantity": 1,
            }
        }
        scrollbar_state = {
            "detected": True,
            "scrollable": True,
            "is_at_bottom": True,
            "is_at_top": False,
            "position_ratio": 1.0,
        }
        drag_edges = []

        def _fake_drag(_scrollbar_state, edge="top", **_kwargs):
            drag_edges.append(edge)
            return {"swiped": True}

        with patch.object(
            trackblazer.bot,
            "get_execution_intent",
            return_value="manual_test",
        ), patch.object(
            trackblazer.bot,
            "push_debug_history",
            return_value=None,
        ), patch.object(
            trackblazer,
            "clear_runtime_ocr_debug",
            return_value=None,
        ), patch.object(
            trackblazer,
            "record_runtime_ocr_debug",
            return_value=None,
        ), patch.object(
            trackblazer,
            "snapshot_runtime_ocr_debug",
            return_value={},
        ), patch.object(
            trackblazer,
            "_build_trackblazer_inventory_debug_entries",
            return_value=[],
        ), patch.object(
            trackblazer,
            "detect_inventory_screen",
            return_value=(True, None, []),
        ), patch.object(
            trackblazer,
            "scan_training_items_inventory",
            return_value=scanned_inventory,
        ), patch.object(
            trackblazer,
            "inspect_trackblazer_inventory_scrollbar",
            side_effect=[scrollbar_state, scrollbar_state],
        ), patch.object(
            trackblazer,
            "_drag_trackblazer_inventory_scrollbar",
            side_effect=_fake_drag,
        ), patch.object(
            trackblazer,
            "_scan_inventory_page",
            return_value=(rescanned_top_page, {}),
        ), patch.object(
            trackblazer,
            "detect_inventory_controls",
            return_value={"confirm_use": {"button_state": "unavailable"}},
        ), patch.object(
            trackblazer,
            "close_training_items_inventory",
            return_value={"closed": True},
        ), patch.object(
            trackblazer.device_action,
            "flush_screenshot_cache",
            return_value=None,
        ), patch.object(
            trackblazer,
            "sleep",
            return_value=None,
        ):
            result = trackblazer.execute_training_items(
                ["top_item", "bottom_item"],
                trigger="test",
                commit_mode="dry_run",
            )

        flow = result.get("trackblazer_inventory_flow") or {}
        self.assertEqual(flow.get("initial_scroll_page"), "bottom")
        self.assertEqual(flow.get("ordered_requested_items"), ["bottom_item", "top_item"])
        self.assertEqual(
            [attempt.get("item_name") for attempt in flow.get("increment_attempts") or []],
            ["bottom_item", "top_item"],
        )
        self.assertEqual(drag_edges, ["top"])
        self.assertTrue(result.get("success"))


if __name__ == "__main__":
    unittest.main()
