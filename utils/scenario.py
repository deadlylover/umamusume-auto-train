import pyautogui
from utils.tools import get_secs
import utils.device_action_wrapper as device_action

def ura():
  race_btn = pyautogui.locateCenterOnScreen("assets/ura/ura_race_btn.png", confidence=0.8, minSearchTime=get_secs(5))
  if race_btn:
    device_action.click(target=(int(race_btn.x), int(race_btn.y)))
