"""冻结诊断输入或显式运行真实基线；默认命令不调用模型。"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def main():
    from evaluation.answer_diagnosis import prepare, run_baseline, validate

    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("prepare")
    commands.add_parser("validate")
    run = commands.add_parser("baseline")
    run.add_argument("--online", action="store_true", required=True)
    run.add_argument("--output", required=True)
    run.add_argument("--ids")
    run.add_argument("--repetition", type=int, default=1)
    run.add_argument("--continued-from")
    pairs = commands.add_parser("pairs")
    pairs.add_argument("--online", action="store_true", required=True)
    pairs.add_argument("--spec", required=True)
    pairs.add_argument("--output", required=True)
    args = parser.parse_args()
    if args.command == "prepare":
        result = prepare()
    elif args.command == "validate":
        result = validate()
    elif args.command == "pairs":
        from evaluation.answer_diagnosis_pairs import run_pairs

        result = asyncio.run(run_pairs(args.spec, args.output))
        result = {
            "status": result["status"],
            "answers": len(result["rows"]),
            "budget": result["budget"],
        }
    else:
        result = asyncio.run(
            run_baseline(args.output, args.ids, args.repetition, continued_from=args.continued_from)
        )
        result = {
            "status": result["status"],
            "answers": len(result["rows"]),
            "budget": result["budget"],
        }
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
