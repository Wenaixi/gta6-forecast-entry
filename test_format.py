"""gta6-twitch-ccu-forecast-01 的格式与确定性自检。

默认对 kit 的 4 个练习 case（p00..p03）跑两遍 main.py，逐条断言输出合法：
  - 恰好 23 行，hour 2..24 各出现一次
  - 每行恰好 5 个有限正数，且不超过 1e9
  - 每行的 5 个分位数非递减
  - 每个文件不超过 65536 字节
  - 两次运行的输出逐字节相同
最后打印每个 case 的 hour-2 与 hour-24 中位数预测；某个 case 没有输出就打印 FAIL。

用法：直接 python test_format.py 即可，脚本会自己设好输入输出目录；
也可以用 MAKEFUN_INPUT_DIR / MAKEFUN_OUTPUT_DIR 指定别的位置。
"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

# kit 的练习输入目录：脚本被单独运行时用这个默认值，省得每次手工设环境变量。
KIT_INPUT = Path(
    r"C:\Users\Administrator\makefun\forecast\gta6-twitch-ccu-forecast-01-kit\input"
)
MAX_BYTES = 65536
EXPECTED_HOURS = list(range(2, 25))
EXPECTED_LEVELS = 5
Q_MAX = 1e9


def run_once(input_dir, output_dir):
    """跑一遍 main.py，返回是否成功。"""
    env = dict(os.environ)
    env["MAKEFUN_INPUT_DIR"] = str(input_dir)
    env["MAKEFUN_OUTPUT_DIR"] = str(output_dir)
    result = subprocess.run(
        [sys.executable, str(Path(__file__).resolve().parent / "main.py")],
        env=env,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print("main.py 退出码", result.returncode)
        print(result.stdout)
        print(result.stderr)
        return False
    return True


def validate_file(path):
    """校验一个 .out 文件，返回 (错误列表, 数据)。"""
    errors = []
    size = path.stat().st_size
    if size > MAX_BYTES:
        errors.append(f"文件 {size} 字节，超过 {MAX_BYTES}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return [f"不是合法 JSON：{exc}"], None

    if not isinstance(data, dict):
        return ["顶层不是对象"], data
    if set(data.keys()) != {"hours"}:
        errors.append(f"顶层键是 {sorted(data.keys())}，应当只有 hours")
    hours = data.get("hours")
    if not isinstance(hours, list):
        return errors + ["hours 不是数组"], data
    if len(hours) != len(EXPECTED_HOURS):
        errors.append(f"行数 {len(hours)}，应当是 23")
    if [row.get("hour") for row in hours] != EXPECTED_HOURS:
        errors.append("hour 不是 2..24 各一次（顺序或取值不对）")

    for row in hours:
        if not isinstance(row, dict) or set(row.keys()) != {"hour", "q"}:
            errors.append(f"第 {row.get('hour')} 行的键不是恰好 hour 与 q")
            continue
        qs = row.get("q")
        if not isinstance(qs, list) or len(qs) != EXPECTED_LEVELS:
            errors.append(f"第 {row['hour']} 行的 q 不是 5 个数")
            continue
        for index, q in enumerate(qs):
            if not isinstance(q, (int, float)) or isinstance(q, bool):
                errors.append(f"第 {row['hour']} 行第 {index} 个分位数不是数字")
                continue
            if q != q or q in (float("inf"), float("-inf")):
                errors.append(f"第 {row['hour']} 行第 {index} 个分位数不是有限数")
            if q <= 0:
                errors.append(f"第 {row['hour']} 行第 {index} 个分位数不大于 0")
            if q > Q_MAX:
                errors.append(f"第 {row['hour']} 行第 {index} 个分位数超过 1e9")
        if all(isinstance(q, (int, float)) and not isinstance(q, bool) for q in qs):
            if any(qs[i] > qs[i + 1] for i in range(len(qs) - 1)):
                errors.append(f"第 {row['hour']} 行的分位数不是非递减")
    return errors, data


def scratch_dir(prefix, parent):
    """在 parent 下建一个临时目录，用完删除。

    不用 tempfile.mkdtemp：它按 0700 建目录，在受限的评测环境里可能连自己也写不进去。
    mkdir 出来的目录沿用父目录的权限，哪里都能写。
    """
    path = Path(parent) / f"{prefix}{os.getpid()}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def main():
    here = Path(__file__).resolve().parent
    input_dir = Path(os.environ.get("MAKEFUN_INPUT_DIR") or KIT_INPUT)
    if not input_dir.is_dir():
        print(f"FAIL: 输入目录 {input_dir} 不存在")
        return 1

    temps = []
    out_a = scratch_dir(".gta6-forecast-a-", here)
    out_b = scratch_dir(".gta6-forecast-b-", here)
    temps.extend([out_a, out_b])
    try:
        for out_dir in (out_a, out_b):
            out_dir.mkdir(parents=True, exist_ok=True)
            if not run_once(input_dir, out_dir):
                print(f"FAIL: main.py 跑不通（输出目录 {out_dir}）")
                return 1

        cases = sorted(p.stem for p in input_dir.glob("*.in"))
        failures = 0
        for case_id in cases:
            out_a_file = out_a / f"{case_id}.out"
            out_b_file = out_b / f"{case_id}.out"
            if not out_a_file.is_file() or not out_b_file.is_file():
                print(f"FAIL: {case_id} 缺少输出文件")
                failures += 1
                continue

            errors, data = validate_file(out_a_file)
            if errors:
                print(f"FAIL: {case_id} 格式不对")
                for error in errors:
                    print(f"  - {error}")
                failures += 1
                continue

            if out_a_file.read_bytes() != out_b_file.read_bytes():
                print(f"FAIL: {case_id} 两次运行输出不一致")
                failures += 1
                continue

            first = data["hours"][0]["q"][2]
            last = data["hours"][-1]["q"][2]
            print(f"PASS: {case_id} hour-2 中位数 {first:g}，hour-24 中位数 {last:g}")

        if failures:
            print(f"\n{failures} 个 case 未通过")
            return 1
        print(f"\n全部 {len(cases)} 个 case 通过：格式合法、行数正确、两次运行逐字节一致")
        return 0
    finally:
        for path in temps:
            shutil.rmtree(path, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
