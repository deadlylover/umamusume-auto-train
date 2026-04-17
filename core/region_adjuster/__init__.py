"""Public helpers for launching the region adjuster UI.

Keep package imports light so submodules such as ``shared`` can be imported
without pulling in the launcher and its heavier runtime dependencies.
"""

from typing import Any, Dict


def run_region_adjuster_session(settings: Dict[str, Any]) -> bool:
  from .launcher import run_region_adjuster_session as _run_region_adjuster_session

  return _run_region_adjuster_session(settings)


__all__ = [
  "run_region_adjuster_session",
]
