import main  # noqa: F401  # Import order avoids existing window-focus circular init path.
from unittest.mock import patch

from core.operator_console import OperatorConsole
from core.trackblazer.planner import append_planner_runtime_transition
from core.trackblazer.models import TurnPlan


def _planner_snapshot():
  turn_plan = TurnPlan(
    decision_path="planner",
    selection_rationale="selected race_candidate via planner scoring [planner_score=9.500]",
    timing={
      "inventory": {
        "timing_open": 0.10,
        "timing_scan": 0.20,
        "timing_controls": 0.03,
        "timing_close": 0.11,
        "timing_total": 0.44,
        "scan_timing": {
          "held_ocr": 0.07,
          "templates": 0.09,
        },
      },
      "shop": {
        "timing_open": 0.08,
        "timing_scan": 0.12,
        "timing_close": 0.08,
        "timing_total": 0.28,
      },
      "skill": {
        "timing_scan": 0.05,
        "timing_total": 0.05,
        "reason": "below threshold",
      },
    },
    debug_summary={
      "inventory_source": "scanned",
      "planner_native_candidate_count": 5,
      "ranked_training_count": 5,
      "shop_item_count": 2,
      "shop_deviation_count": 1,
      "execution_item_count": 2,
    },
    planner_metadata={
      "runtime_path": "planner_runtime",
      "inventory_source": "scanned",
    },
  )
  return {
    "sub_phase": "pre_training",
    "state_summary": {
      "trackblazer_inventory_pre_shop_flow": {
        "opened": True,
        "closed": True,
        "timing_open": 0.09,
        "timing_scan": 0.18,
        "timing_close": 0.10,
        "timing_total": 0.37,
      },
      "trackblazer_inventory_flow": {
        "opened": True,
        "closed": True,
        "timing_open": 0.10,
        "timing_scan": 0.20,
        "timing_controls": 0.03,
        "timing_close": 0.11,
        "timing_total": 0.44,
      },
      "trackblazer_shop_flow": {
        "entered": True,
        "closed": True,
        "timing_open": 0.08,
        "timing_scan": 0.12,
        "timing_close": 0.08,
        "timing_total": 0.28,
      },
      "skill_purchase_flow": {
        "skipped": True,
        "reason": "below threshold",
        "timing_total": 0.05,
      },
      "trackblazer_planner_runtime": {
        "runtime_path": "planner_runtime",
        "latest_observation_id": "obs-123",
        "fallback_count": 1,
        "last_fallback_reason": "shop_entry_failed_once",
        "pending_skill_scan": {
          "status": "processing",
          "reason": "background_preview_processing",
          "captured_sp": 240,
        },
        "pending_shop_scan": {
          "status": "queued",
          "reason": "planner_refresh_missing_shop_state",
          "source": "planner_refresh",
          "shop_status": "stale",
          "shop_turn_key": "Senior Year Early Jul|12",
        },
        "transition_breadcrumbs": [
          {
            "step_id": "await_operator_review",
            "status": "completed",
            "note": "operator_confirmed",
            "started_at": 1710000000.100,
            "finished_at": 1710000000.420,
            "duration": 0.32,
            "details": {"trigger": "test_review"},
          },
          {
            "step_id": "execute_shop_purchases",
            "status": "started",
            "note": "shop purchase running",
            "started_at": 1710000001.250,
            "details": {"timing_open": 0.08, "timing_scan": 0.12},
          },
        ],
      },
      "trackblazer_planner_state": {
        "decision_path": "planner",
        "turn_plan": turn_plan.to_snapshot(),
      },
    },
    "trackblazer_planner_runtime": {
      "runtime_path": "planner_runtime",
      "latest_observation_id": "obs-123",
      "fallback_count": 1,
      "last_fallback_reason": "shop_entry_failed_once",
      "pending_skill_scan": {
        "status": "processing",
        "reason": "background_preview_processing",
        "captured_sp": 240,
      },
      "pending_shop_scan": {
        "status": "queued",
        "reason": "planner_refresh_missing_shop_state",
        "source": "planner_refresh",
        "shop_status": "stale",
        "shop_turn_key": "Senior Year Early Jul|12",
      },
      "transition_breadcrumbs": [
        {
          "step_id": "await_operator_review",
          "status": "completed",
          "note": "operator_confirmed",
          "started_at": 1710000000.100,
          "finished_at": 1710000000.420,
          "duration": 0.32,
          "details": {"trigger": "test_review"},
        },
        {
          "step_id": "execute_shop_purchases",
          "status": "started",
          "note": "shop purchase running",
          "started_at": 1710000001.250,
          "details": {"timing_open": 0.08, "timing_scan": 0.12},
        },
      ],
    },
    "trackblazer_planner_state": {
      "decision_path": "planner",
      "turn_plan": turn_plan.to_snapshot(),
    },
  }


def _planner_runtime_state():
  return {
    "turn_metrics": {
      "current": {
        "turn_label": "Senior Year Early Jul / 12",
        "status": "in_progress",
        "started_at": 0.0,
        "total_duration": 1.25,
        "selected_action": {
          "func": "do_training",
          "training_name": "speed",
        },
        "category_totals": {
          "state": 0.42,
          "scan": 0.77,
        },
        "steps": [
          {
            "label": "Trackblazer inventory",
            "duration": 0.44,
            "status": "completed",
            "detail": "items=2 | source=scanned",
            "data": {
              "timing_open": 0.10,
              "timing_scan": 0.20,
              "timing_controls": 0.03,
              "timing_close": 0.11,
              "cached": False,
            },
          }
        ],
      },
    },
  }


def _manual_snapshot():
  return {
    "sub_phase": "manual_inventory_selection_test",
    "state_summary": {
      "trackblazer_inventory_flow": {
        "opened": True,
        "closed": True,
        "timing_open": 0.14,
        "timing_scan": 0.31,
        "timing_increments": 0.09,
        "timing_controls": 0.04,
        "timing_confirm": 0.02,
        "timing_close": 0.10,
        "timing_total": 0.70,
        "reason": "manual_prepare_training_items",
        "open_result": {
          "timing": {
            "click_breakdown": {
              "clicked": True,
              "backend": "adb",
              "target": [224, 1399],
              "resolved_click_point": [224, 1399],
              "target_kind": "point",
              "backend_debug": {"device_id": "127.0.0.1:5555"},
              "history_context": "timing_check",
              "note": "debug-only noise",
            },
          },
        },
        "scan_timing": {
          "held_ocr": 0.12,
          "templates": 0.15,
        },
      },
    },
  }


def main():
  console = OperatorConsole()

  runtime_state = {}
  with patch("core.trackblazer.planner.time.time", side_effect=[100.0, 100.6]):
    append_planner_runtime_transition(runtime_state, step_id="execute_main_action", step_type="execute_main_action", status="started")
    append_planner_runtime_transition(runtime_state, step_id="execute_main_action", step_type="execute_main_action", status="completed")
  breadcrumbs = (runtime_state.get("trackblazer_planner_runtime") or {}).get("transition_breadcrumbs") or []
  assert len(breadcrumbs) == 2, breadcrumbs
  assert breadcrumbs[0].get("started_at") == 100.0, breadcrumbs
  assert breadcrumbs[0].get("finished_at") is None, breadcrumbs
  assert breadcrumbs[1].get("started_at") == 100.0, breadcrumbs
  assert breadcrumbs[1].get("finished_at") == 100.6, breadcrumbs
  assert breadcrumbs[1].get("duration") == 0.6, breadcrumbs

  planner_text = console._format_timing(_planner_snapshot(), runtime_state=_planner_runtime_state())
  assert "=== Current Turn ===" in planner_text, planner_text
  assert "=== Planner Runtime ===" in planner_text, planner_text
  assert "pending_skill" in planner_text, planner_text
  assert "Planner Transitions:" in planner_text, planner_text
  assert "=== Planner Snapshot: Inventory ===" in planner_text, planner_text
  assert "=== Inventory Flow (Pre-Shop) ===" in planner_text, planner_text
  assert "open=0.100s" in planner_text, planner_text
  assert "->" in planner_text and "(0.320s)" in planner_text, planner_text

  manual_text = console._format_timing(_manual_snapshot(), runtime_state={})
  assert "=== Inventory Flow ===" in manual_text, manual_text
  assert "increments" in manual_text, manual_text
  assert "confirm" in manual_text, manual_text
  assert "=== Scan Breakdown ===" in manual_text, manual_text
  assert "backend_debug" not in manual_text, manual_text
  assert "history_context" not in manual_text, manual_text
  assert "debug-only noise" not in manual_text, manual_text
  assert "[224, 1399]" not in manual_text, manual_text
  assert "target_kind" not in manual_text, manual_text
  assert "backend        adb" not in manual_text, manual_text
  assert "Planner Runtime" not in manual_text, manual_text

  post_action_text = console._format_post_action_timing({
    "post_action_resolution": {
      "source_action": "do_race",
      "status": "completed",
      "outcome": "stable_lobby_confirmed",
      "turn_label": "Senior Year Late Mar / 19",
      "started_at": 1710000000.0,
      "completed_at": 1710000014.494,
      "timing_total": 14.494,
      "timing_event_choice": 0.0,
      "timing_screenshot_capture": 0.6,
      "timing_lobby_detect": 1.118,
      "timing_popup_checks": 11.2,
      "timing_popup_handlers": 0.0,
      "timing_followup_wait": 0.0,
      "timing_generic_recovery": 0.0,
      "timing_idle_sleep": 1.016,
      "loop_count": 3,
      "timeline_entries": [],
    },
  })
  assert "popup_checks" in post_action_text, post_action_text
  assert "screenshot" in post_action_text, post_action_text
  assert "other" in post_action_text, post_action_text

  print("operator_console_timing_checks: ok")


if __name__ == "__main__":
  main()
