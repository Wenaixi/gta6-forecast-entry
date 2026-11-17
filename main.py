"""gta6-twitch-ccu-forecast-01: weighted-shape forecast of GTA VI's launch-day Twitch viewers.

程序读取输入目录里的每个 <id>.in（一局比赛的 case JSON），对发布日的第 2~24 个小时各给出
5 个分位数，写到输出目录的 <id>.out。算法是“加权形状模型”：用过去大作的逐小时曲线学习
“相对第 1 小时的形状”，再按“第 1 小时观看数越接近该 case 第 1 小时观看数，权重越大”的方式
把各可比对象加权混合，最后在第 1 小时的真实观看数上放大。

算法步骤（与任务书逐条对应）：
  1. 每个 comparable 的 hourly 按 from 对到它自己的 day_start，落进小时桶 h=1..24，
     桶 h 覆盖 [day_start+(h-1)h, day_start+h h)；第 1 小时必须存在。
  2. 只保留真实发布：unlock 不以 "Not a launch" 开头的 comparable。
  3. 对数比率 l_h = ln(viewers_h / viewers_1)，h=2..24。
  4. 高斯核权重 w = exp(-0.5 * (|ln(p1_case) - ln(p1_comp)| / 1.0)^2)。
  5. 每小时 center_h = 加权平均 l_h；sigma_h = max(1.4826 * 加权中位绝对偏差, 0.2)。
  6. q_tau(h) = p1 * exp(center_h + z_tau * sigma_h)，p1 = max(第 1 小时观看数, 1.0)。
  7. 某小时有效加权样本 < 5 时退回基线：q_tau(h) = p1 * exp(z_tau * 0.15 * sqrt(h-1))。
  8. 强制 q 非递减且 > 0，并钳制在 1e9 以内。

只依赖 Python 标准库，没有随机数、没有网络，同一份输入两次运行的输出逐字节相同。

在评测容器里输入输出目录是 /input 与 /output，本地运行时用环境变量代替：
    python main.py
"""

import json
import math
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

INPUT_DIR = Path(os.environ.get("MAKEFUN_INPUT_DIR", "/input"))
OUTPUT_DIR = Path(os.environ.get("MAKEFUN_OUTPUT_DIR", "/output"))

# 评测按 5% / 25% / 50% / 75% / 95% 这五个分位点打分，一行的顺序固定成这样。
LEVELS = [0.05, 0.25, 0.5, 0.75, 0.95]
# 标准正态分布在这五个分位点上的值。
Z = [-1.6449, -0.6745, 0.0, 0.6745, 1.6449]
# 输出里每个观看数允许的最大值。
Q_MAX = 1e9
# 输出里每个观看数允许的最小值（取整后仍然必须为正）。
Q_MIN = 0.01
# 高斯核的带宽，按任务书取 1.0（自然对数尺度上“第 1 小时规模差一个对数单位”）。
KERNEL_BANDWIDTH = 1.0
# 加权形状模型的散布下限，避免有效样本少时把分布说得过于自信。
SIGMA_FLOOR = 0.2
# 退回基线时沿用的随机游走散布，与房子的基线一致。
BASELINE_SIGMA = 0.15
# 有效加权样本少于这个数就退回基线。
MIN_EFFECTIVE_SAMPLES = 5.0
# 加权中位绝对偏差到标准差的换算系数（正态分布下 1.4826 倍 MAD 近似标准差）。
MAD_SCALE = 1.4826


def parse_time(text):
    """把 ISO 8601 时间（含结尾的 Z）解析成带时区的时间，统一按 UTC 计算。"""
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def hour_index(point_from, day_start):
    """把一个数据点落在发布日起点的第几个小时：向下取整到整点，落在 1..24 之外就不是发布日的数据。"""
    offset = (parse_time(point_from) - day_start).total_seconds() / 3600.0
    return int(math.floor(offset)) + 1


def bucket_comparable(comparable):
    """把一个 comparable 的 hourly 点按 day_start 对齐成 {h: viewers}，h=1..24。

    同一小时有几个点时取它们的均值（与“一小时的平均观看数”定义一致）；没有有效点的小时不放进桶
    里，那个可比对象就不会为那个小时贡献样本。
    """
    day_start = parse_time(comparable["day_start"])
    sums = {}
    counts = {}
    for point in comparable.get("hourly") or []:
        h = hour_index(point["from"], day_start)
        if 1 <= h <= 24:
            sums[h] = sums.get(h, 0.0) + float(point["viewers"])
            counts[h] = counts.get(h, 0) + 1
    return {h: sums[h] / counts[h] for h in sums}


def is_real_launch(comparable):
    """只要真实发布的曲线：unlock 以 "Not a launch" 开头的是发布前预热数据，剔除。"""
    unlock = comparable.get("unlock") or ""
    return not unlock.lstrip().startswith("Not a launch")


def log_ratio(shape, h):
    """l_h = ln(viewers_h / viewers_1)；两者都至少按 1 计，避免对数为负无穷。"""
    return math.log(max(shape[h], 1.0) / max(shape[1], 1.0))


def gaussian_weight(p1_case, p1_comparable):
    """w = exp(-0.5 * (|ln(p1_case) - ln(p1_comp)| / 1.0)^2)。"""
    delta = math.log(p1_case) - math.log(max(p1_comparable, 1.0))
    return math.exp(-0.5 * (delta / KERNEL_BANDWIDTH) ** 2)


def weighted_mean(values, weights):
    """加权平均；权重全为 0 时返回 None，交给调用方兜底。"""
    total = sum(weights)
    if total <= 0:
        return None
    return sum(v * w for v, w in zip(values, weights)) / total


def weighted_median(values, weights):
    """加权中位数：按数值升序排列，累计权重第一次达到总权重一半处取这个数值。

    这是加权中位数的标准定义；任务书说的“找累计权重 0.5 处的值”就是这个意思。
    """
    total = sum(weights)
    if total <= 0:
        return None
    half = total / 2.0
    cumulative = 0.0
    for value, weight in sorted(zip(values, weights)):
        cumulative += weight
        if cumulative >= half:
            return value
    return sorted(zip(values, weights))[-1][0]


def round2(x):
    """保留两位小数，四舍五入（与房子基线的 Math.round(x*100)/100 一致）。

    JSON 里写成 23357.0 会多占字节，整数就写整数，和基线的写法保持一致。
    """
    scaled = x * 100.0
    whole = math.floor(scaled)
    if scaled - whole >= 0.5:
        whole += 1
    value = whole / 100.0
    return int(value) if value.is_integer() else value


def collect_samples(case):
    """取出全部可用的可比对象，算好它们的小时形状与权重。

    返回 [(shape, weight)]：shape 是 {h: viewers}，weight 是按第 1 小时规模算的高斯核权重。
    第 1 小时缺失或不是真实发布的可比对象直接跳过。
    """
    p1_case = max(float(case["known"][0]["viewers"]), 1.0)
    samples = []
    for comparable in case.get("comparables") or []:
        if not is_real_launch(comparable):
            continue
        shape = bucket_comparable(comparable)
        if 1 not in shape:
            continue
        samples.append((shape, gaussian_weight(p1_case, shape[1])))
    return samples


def shape_forecast(samples, p1):
    """用加权形状模型给出 2..24 小时的 (center, sigma)；样本不足的小时返回 None。"""
    result = {}
    for h in range(2, 25):
        pairs = []
        for shape, weight in samples:
            if h in shape:
                pairs.append((log_ratio(shape, h), weight))
        if not pairs:
            continue
        values = [v for v, _ in pairs]
        weights = [w for _, w in pairs]
        # 有效加权样本：该小时真正贡献了样本的那些可比对象的权重总和。
        effective = sum(weights)
        if effective < MIN_EFFECTIVE_SAMPLES:
            continue
        center = weighted_mean(values, weights)
        if center is None:
            continue
        deviations = [abs(v - center) for v in values]
        mad = weighted_median(deviations, weights)
        if mad is None:
            continue
        sigma = max(MAD_SCALE * mad, SIGMA_FLOOR)
        result[h] = (center, sigma)
    return result


def fallback_hours(p1):
    """基线兜底：q_tau(h) = p1 * exp(z_tau * 0.15 * sqrt(h-1))。"""
    return {
        h: [p1 * math.exp(z * BASELINE_SIGMA * math.sqrt(h - 1.0)) for z in Z]
        for h in range(2, 25)
    }


def finalize(qs):
    """把五个分位数收口：正数、非递减、不超过 1e9，并保留两位小数。"""
    ordered = []
    previous = 0.0
    for q in qs:
        value = min(max(q, 0.0), Q_MAX)
        value = round2(value)
        if value < Q_MIN:
            value = Q_MIN
        if value < previous:
            value = previous
        if value > Q_MAX:
            value = Q_MAX
        previous = value
        ordered.append(value)
    return ordered


def forecast_case(case):
    """对一局 case 给出 23 个小时的五个分位数，返回可以写成 <id>.out 的字典。"""
    known = case.get("known") or []
    first = next((row for row in known if row.get("hour") == 1), None)
    if first is None:
        raise ValueError(f"{case.get('id')} has no hour-1 print")
    p1 = max(float(first["viewers"]), 1.0)

    samples = collect_samples(case)
    shaped = shape_forecast(samples, p1)
    fallback = fallback_hours(p1)

    hours = []
    for h in range(2, 25):
        if h in shaped:
            center, sigma = shaped[h]
            qs = [p1 * math.exp(center + z * sigma) for z in Z]
        else:
            qs = fallback[h]
        hours.append({"hour": h, "q": finalize(qs)})
    return {"hours": hours}


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    # 按文件名排序，保证两次运行写出的字节完全一致。
    for path in sorted(INPUT_DIR.glob("*.in")):
        case = json.loads(path.read_text(encoding="utf-8"))
        forecast = forecast_case(case)
        text = json.dumps(forecast, separators=(",", ":"), ensure_ascii=False) + "\n"
        (OUTPUT_DIR / f"{path.stem}.out").write_text(text, encoding="utf-8")


if __name__ == "__main__":
    sys.exit(main())
