"""独立对照用的短生命周期本地基线；退出后释放Chroma和模型资源。"""
import argparse
import json
import sys
from pathlib import Path
from time import perf_counter
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from business.readonly_tools import policy_result


def main(output):
    query = '金卡会员未标注延长退货，是否适用15天？'
    started = perf_counter()
    payload = json.loads(policy_result(query))
    cold = (perf_counter() - started) * 1000
    warm = []
    for _ in range(3):
        started = perf_counter()
        policy_result(query)
        warm.append((perf_counter() - started) * 1000)
    Path(output).write_text(json.dumps({'payload': payload, 'cold_ms': cold, 'warm_ms': warm}, ensure_ascii=False), encoding='utf-8')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', required=True)
    main(parser.parse_args().output)
