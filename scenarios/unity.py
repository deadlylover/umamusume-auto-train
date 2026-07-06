import utils.constants as constants
import utils.device_action_wrapper as device_action
import core.config as config
from utils.shared import CleanDefaultDict
from utils.log import error, info, warning, debug, debug_window
from utils.tools import get_secs, sleep

TEAM_MATCHUP_TEMPLATES = {
  "affinity_0": "assets/unity/unity_affinity_0.png",
  "affinity_1": "assets/unity/unity_affinity_1.png",
  "affinity_2": "assets/unity/unity_affinity_2.png",
  "affinity_3": "assets/unity/unity_affinity_3.png",
}

if not hasattr(config, "UNITY_MINIMUM_MATCHUP_SCORE"):
  config.UNITY_MINIMUM_MATCHUP_SCORE = 11

def find_best_match(matchups):
  best_match = matchups[0]
  best_match_score = int(best_match["score"])
  for matchup in matchups:
    score = int(matchup["score"])
    if score > config.UNITY_MINIMUM_MATCHUP_SCORE:
      return matchup
    elif score > best_match_score:
      best_match = matchup
      best_match_score = score
  return best_match

def click_zenith_race_button():
  # Try both templates; the button can flash between variants.
  button_region = constants.SCREEN_BOTTOM_BBOX
  for _ in range(5):
    if device_action.locate_and_click("assets/unity/zenith_race.png", min_search_time=get_secs(1), region_ltrb=button_region):
      return True
    if device_action.locate_and_click("assets/unity/zenith_race2.png", min_search_time=get_secs(1), region_ltrb=button_region):
      return True
    sleep(0.5)
  return False

def scan_unity_matchups():
  """Read-only Unity cup opponent scan.

  Detects the matchup screen, evaluates team affinity against each candidate
  opponent, and returns a plan describing what a commit *would* do — without
  starting a race. Navigating into each opponent's affinity view and cancelling
  back out is non-destructive, so this is safe to run under check_only for a
  preview. The returned plan is consumed by ``commit_unity_race``.

  Returns a dict with ``kind`` one of:
    - ``"opponent_select"``: opponents scored; includes ``best_match``,
      ``matchups``, and ``select_opponent_mouse_pos``.
    - ``"zenith"``: S-rank / zenith race, no opponent selection needed.
  Raises ``ValueError`` for the same unrecoverable states the legacy flow did.
  """
  tries = 0
  while True:
    device_action.flush_screenshot_cache()
    screenshot = device_action.screenshot()
    select_opponent_btn = device_action.locate("assets/unity/select_opponent_btn.png")
    s_rank_opponent = device_action.locate("assets/unity/s_rank_opponent.png", region_ltrb=constants.SCREEN_MIDDLE_BBOX)
    sleep(0.25)
    if select_opponent_btn:
      break
    elif s_rank_opponent:
      break
    tries += 1
    if tries > 20:
      raise ValueError("Select opponent button not found, please report this.")
  rank_matches = device_action.match_template("assets/unity/team_rank.png", screenshot)
  if not select_opponent_btn and not s_rank_opponent:
    raise ValueError("Select opponent and zenith race button not found, please report this.")
  elif select_opponent_btn:
    select_opponent_mouse_pos = (select_opponent_btn[0], select_opponent_btn[1])
  elif s_rank_opponent:
    info(f"Zenith indicator detected (S rank opponent) at {s_rank_opponent}.")
    return {
      "kind": "zenith",
      "s_rank_opponent": s_rank_opponent,
      "reason": "Zenith (S-rank) race — no opponent selection required.",
    }
  if len(rank_matches) == 0:
    raise ValueError("Team rank not found, please report this.")
  matchups = []
  # sort matchups by Y coords
  rank_matches.sort(key=lambda x: x[1])
  # for every opponent team
  for rank_match in rank_matches:
    count = CleanDefaultDict()
    x, y, w, h = rank_match
    offset_x = constants.GAME_WINDOW_BBOX[0]
    offset_y = constants.GAME_WINDOW_BBOX[1]
    x = x + offset_x + w // 2
    y = y + offset_y + h // 2
    device_action.click(target=(x, y))
    count["mouse_pos"] = (x, y)
    device_action.click(select_opponent_mouse_pos)
    tries = 0
    while tries < 10:
      if device_action.locate("assets/unity/unity_tazuna.png"):
        break
      tries += 1
      if tries > 10:
        raise ValueError("Affinity screen not found, please report this.")
      device_action.flush_screenshot_cache()

    matchup_screenshot = device_action.screenshot(region_xywh=constants.UNITY_TEAM_MATCHUP_REGION)
    debug_window(matchup_screenshot, save_name="unity_team_matchup")

    # find all affinity vs opponent team
    for name, path in TEAM_MATCHUP_TEMPLATES.items():
      matches = device_action.match_template(path, matchup_screenshot)
      # if affinity found, count it
      for match in matches:
        count["score"] += int(name.split("_")[1])
        count[name] += 1
    matchups.append(count)
    # Cancel back out to the opponent list — read-only navigation.
    device_action.locate_and_click("assets/buttons/cancel_btn.png")
    # max of 5 matches, if all is double circle, stop looking at the others since they're the same
    if count["affinity_3"] > 4:
      break
  debug(f"Unity matchups: {matchups}")
  best_match = find_best_match(matchups)
  return {
    "kind": "opponent_select",
    "select_opponent_mouse_pos": select_opponent_mouse_pos,
    "best_match": best_match,
    "matchups": matchups,
    "reason": f"Best matchup score {best_match['score']} at team {best_match['mouse_pos']}.",
  }


def commit_unity_race(plan):
  """Destructive commit of a plan produced by ``scan_unity_matchups``.

  Clicks through the chosen opponent (or the zenith race button) and starts the
  race. This is the only part of the Unity cup flow that actually enters a race,
  so it must run only after the execution-intent review has approved the turn.
  """
  plan = plan or {}
  kind = plan.get("kind")
  if kind == "zenith":
    sleep(1)
    if click_zenith_race_button():
      unity_race_start()
      return True
    warning("Zenith Race button not found; skipping Unity race start.")
    return False
  if kind == "opponent_select":
    best_match = plan.get("best_match") or {}
    select_opponent_mouse_pos = plan.get("select_opponent_mouse_pos")
    mouse_pos = best_match.get("mouse_pos")
    if not mouse_pos or not select_opponent_mouse_pos:
      warning("Unity commit missing click targets; skipping race start.")
      return False
    device_action.click(target=(mouse_pos[0], mouse_pos[1]))
    device_action.click(select_opponent_mouse_pos)
    unity_race_start()
    return True
  warning(f"Unity commit called with unknown plan kind: {kind!r}")
  return False


def unity_cup_function():
  """Backward-compatible one-shot Unity cup flow: scan then immediately commit.

  Prefer the split ``scan_unity_matchups`` / ``commit_unity_race`` pair when the
  race start needs to be gated behind the execution-intent review.
  """
  plan = scan_unity_matchups()
  if not plan or plan.get("kind") in (None, "none"):
    return False
  return commit_unity_race(plan)

def unity_race_start():
  sleep(1)
  # macOS-specific variant can differ slightly; try it first, then fallback.
  if not device_action.locate_and_click("assets/unity/start_unity_match_macos.png", min_search_time=get_secs(2)):
    device_action.locate_and_click("assets/unity/start_unity_match.png", min_search_time=get_secs(2))
  sleep(1)
  device_action.locate_and_click("assets/unity/see_results.png", min_search_time=get_secs(20), region_ltrb=constants.SCREEN_BOTTOM_BBOX)
  sleep(2)
  device_action.locate_and_click("assets/buttons/skip_btn.png", min_search_time=get_secs(5), region_ltrb=constants.SCREEN_BOTTOM_BBOX)
