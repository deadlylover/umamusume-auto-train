from pathlib import Path
from typing import Any, Dict, List, Tuple


def resolve_region_adjuster_profiles(settings: Dict[str, Any]) -> Tuple[Dict[str, str], str, str]:
  raw_profiles = settings.get("profiles") or {}
  fallback_path = str(settings.get("overrides_path") or "data/region_overrides.json")

  profiles: Dict[str, str] = {}
  if isinstance(raw_profiles, dict):
    for name, path in raw_profiles.items():
      if not name or not path:
        continue
      profiles[str(name)] = str(path)

  if not profiles:
    profiles = {"default": fallback_path}

  active_profile = str(settings.get("active_profile") or "").strip()
  if not active_profile or active_profile not in profiles:
    active_profile = next(iter(profiles))

  return profiles, active_profile, profiles[active_profile]


def build_profile_tabs(profiles: Dict[str, str], active_profile: str) -> List[Dict[str, Any]]:
  tabs = []
  for name, path in profiles.items():
    tabs.append(
      {
        "name": name,
        "path": str(path),
        "active": name == active_profile,
      }
    )
  return tabs


def ensure_parent_dir(path_str: str) -> None:
  Path(path_str).parent.mkdir(parents=True, exist_ok=True)
