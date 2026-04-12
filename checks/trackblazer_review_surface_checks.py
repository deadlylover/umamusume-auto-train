from core.trackblazer.models import ExecutionStep, TurnPlan


def main():
  turn_plan = TurnPlan(
    decision_path="planner",
    step_sequence=[
      ExecutionStep(
        step_id="await_operator_review",
        step_type="await_operator_review",
        intent="review_current_turn",
      ),
      ExecutionStep(
        step_id="execute_skill_purchases",
        step_type="execute_skill_purchases",
        intent="commit_skill_purchase_plan",
        planned_clicks=[{"label": "Open skills menu"}, {"label": "Buy Corner Recovery ○"}],
      ),
      ExecutionStep(
        step_id="execute_shop_purchases",
        step_type="execute_shop_purchases",
        intent="buy_planned_trackblazer_items",
        success_transition="shop_purchase_complete",
        failure_transition="shop_purchase_failed",
        planned_clicks=[{"label": "Open shop for purchases"}, {"label": "Buy Motivating Megaphone (55 coins)"}],
      ),
      ExecutionStep(
        step_id="execute_main_action",
        step_type="execute_main_action",
        intent="run_selected_action",
        planned_clicks=[{"label": "Open race menu"}],
      ),
    ],
    race_plan={
      "selected_action": {
        "func": "do_race",
        "race_name": "Takarazuka Kinen",
      }
    },
  )

  planned_actions = turn_plan.to_planned_actions()
  execution_steps = list(planned_actions.get("execution_steps") or [])
  assert [step.get("step_type") for step in execution_steps] == [
    "await_operator_review",
    "execute_skill_purchases",
    "execute_shop_purchases",
    "execute_main_action",
  ]

  text = turn_plan.to_turn_discussion(
    {
      "turn_label": "Classic Year Late Jun / 13",
      "scenario_name": "trackblazer",
      "execution_intent": "execute",
      "state_summary": {
        "year": "Classic Year Late Jun",
        "skill_purchase_check": {
          "should_check": True,
          "reason": "Skill scan complete. Purchase is queued before the main action.",
        },
      },
      "selected_action": {
        "func": "do_race",
        "race_name": "Takarazuka Kinen",
      },
      "planned_clicks": turn_plan.to_planned_clicks(),
    }
  )

  assert "Execution Flow" in text
  assert "execute_skill_purchases" in text
  assert "execute_shop_purchases" in text
  assert "clicks=Open shop for purchases -> Buy Motivating Megaphone (55 coins)" in text

  print("trackblazer_review_surface_checks: ok")


if __name__ == "__main__":
  main()
