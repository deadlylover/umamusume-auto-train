import argparse
import sys
from pathlib import Path

from .app import run_app


def main():
  parser = argparse.ArgumentParser(description="Launch the Uma OCR region adjuster UI.")
  parser.add_argument("--context", required=True, help="Path to the serialized region context JSON.")
  args = parser.parse_args()

  context_path = Path(args.context)
  if not context_path.exists():
    print(f"Context file not found: {context_path}")
    sys.exit(2)

  try:
    run_app(str(context_path))
  except RuntimeError as exc:
    print(f"Region adjuster failed: {exc}")
    sys.exit(1)


if __name__ == "__main__":
  main()
