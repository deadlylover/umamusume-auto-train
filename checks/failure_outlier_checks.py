from core.state import _correct_failure_outliers


def _results(spd, sta, pwr, guts, wit=0):
  return {
    "spd": {"failure": spd},
    "sta": {"failure": sta},
    "pwr": {"failure": pwr},
    "guts": {"failure": guts},
    "wit": {"failure": wit},
  }


def main():
  high_spike = _correct_failure_outliers(_results(70, 0, 0, 0))
  assert high_spike["spd"]["failure"] == 0, high_spike
  assert high_spike["spd"]["failure_corrected_from"] == 70, high_spike

  low_dip = _correct_failure_outliers(_results(70, 70, 0, 70))
  assert low_dip["pwr"]["failure"] == 70, low_dip
  assert low_dip["pwr"]["failure_corrected_from"] == 0, low_dip

  ambiguous = _correct_failure_outliers(_results(70, 0, 20, 0))
  assert "failure_corrected_from" not in ambiguous["spd"], ambiguous
  assert ambiguous["spd"]["failure"] == 70, ambiguous

  two_high = _correct_failure_outliers(_results(70, 70, 0, 0))
  assert all(
    "failure_corrected_from" not in data
    for data in two_high.values()
  ), two_high

  wit_ignored = _correct_failure_outliers(_results(0, 0, 0, 0, wit=70))
  assert wit_ignored["wit"]["failure"] == 70, wit_ignored

  print("failure outlier checks passed")


if __name__ == "__main__":
  main()
