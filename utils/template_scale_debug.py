import argparse
from pathlib import Path
import sys

import cv2

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
  sys.path.insert(0, str(ROOT_DIR))

from utils.device_action_wrapper import best_template_match


def build_scale_list(min_scale: float, max_scale: float, step: float):
  scales = []
  current = min_scale
  while current <= max_scale + 1e-9:
    scales.append(round(current, 4))
    current += step
  return scales


def main():
  parser = argparse.ArgumentParser(description="Sweep template scales against a saved region image.")
  parser.add_argument("--region", required=True, help="Path to the captured search region image.")
  parser.add_argument("--template", required=True, help="Path to the template asset.")
  parser.add_argument("--min-scale", type=float, default=0.8)
  parser.add_argument("--max-scale", type=float, default=1.5)
  parser.add_argument("--step", type=float, default=0.01)
  parser.add_argument("--top", type=int, default=10, help="How many top matches to print.")
  parser.add_argument("--output", help="Optional path for an annotated best-match image.")
  args = parser.parse_args()

  region = cv2.imread(args.region, cv2.IMREAD_COLOR)
  if region is None:
    raise FileNotFoundError(f"Could not read region image: {args.region}")
  region = cv2.cvtColor(region, cv2.COLOR_BGR2RGB)

  scales = build_scale_list(args.min_scale, args.max_scale, args.step)
  results = []
  for scale in scales:
    match = best_template_match(args.template, region, template_scales=[scale])
    if match is None:
      continue
    results.append(match)

  if not results:
    raise RuntimeError("No valid scale candidates were evaluated.")

  results.sort(key=lambda item: item["score"], reverse=True)

  print(f"Region: {args.region}")
  print(f"Template: {args.template}")
  print("Top matches:")
  for item in results[: args.top]:
    print(
      f"  scale={item['scale']:.3f} score={item['score']:.6f} "
      f"loc={item['location']} size={item['size']}"
    )

  best = results[0]
  if args.output:
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    annotated = cv2.cvtColor(region.copy(), cv2.COLOR_RGB2BGR)
    x, y = best["location"]
    w, h = best["size"]
    cv2.rectangle(annotated, (x, y), (x + w, y + h), (0, 255, 0), 2)
    cv2.putText(
      annotated,
      f"scale={best['scale']:.3f} score={best['score']:.4f}",
      (x, max(18, y - 8)),
      cv2.FONT_HERSHEY_SIMPLEX,
      0.6,
      (0, 255, 0),
      2,
      cv2.LINE_AA,
    )
    cv2.imwrite(str(output_path), annotated)
    print(f"Annotated output: {output_path}")


if __name__ == "__main__":
  main()
