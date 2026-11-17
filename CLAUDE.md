# gta6-forecast-entry — 项目记忆库

## 这是什么

make.fun 竞赛 `gta6-twitch-ccu-forecast-01` 的参赛仓库：预测 GTA VI 发布日（2026-11-19 UTC）
第 2~24 小时 Twitch「Grand Theft Auto VI」分类的并发观看人数，每小时给出 5 个分位数。

评分是 log 尺度上的 pinball loss（越低越好），并且必须严格低于房子基线才有奖金资格。

## 交付物

| 文件 | 作用 |
| --- | --- |
| `main.py` | 入口。读 `$MAKEFUN_INPUT_DIR`（默认 `/input`）下所有 `*.in`，写 `$MAKEFUN_OUTPUT_DIR`（默认 `/output`）下 `<id>.out` |
| `test_format.py` | 对 kit 的 4 个练习 case 跑两遍，断言格式、行数、取值、字节数、两次运行一致，并打印 hour-2 / hour-24 中位数 |
| `README.md` | 竞赛 README，按官方模板填，badge 里的 `{owner}/{repo}` 已替换为 `Wenaixi/gta6-forecast-entry` |
| `LICENSE` | MIT，版权人 Wenaixi |

## 模型（必须照此实现，不要擅自调参）

加权形状模型，八步：

1. 每个 comparable 的 `hourly` 按 `from` 对齐到它自己的 `day_start`，落进小时桶 h=1..24
   （桶 h = `[day_start+(h-1)h, day_start+h h)`）；hour-1 必须存在。
2. 过滤：只保留 `unlock` 不以 `Not a launch` 开头的（真实发布）。
3. 对数比率 `l_h = ln(viewers_h / viewers_1)`，h=2..24。
4. 高斯核权重 `w = exp(-0.5 * (|ln(p1_case) - ln(p1_comp)| / 1.0)^2)`。
5. 每小时 `center_h` = 加权平均 `l_h`；`sigma_h = max(1.4826 * 加权中位绝对偏差, 0.2)`。
6. `q_tau(h) = p1 * exp(center_h + z_tau * sigma_h)`，p1 = max(hour-1 print, 1.0)，
   z = (-1.6449, -0.6745, 0, 0.6745, 1.6449)。
7. 某小时有效加权样本 < 5 时退回基线：`q_tau(h) = p1 * exp(z_tau * 0.15 * sqrt(h-1))`。
8. 强制 q 非递减且 > 0，上限 1e9。

## 关键决策与理由

- **不调参**：留一可比交叉验证显示，把 sigma 系数/下限微调最多只改善约 0.7%
  （0.7226 → 0.7175）。任务书明确写死 1.4826 与 0.2，且样本内微调有过拟合风险，因此严格照规范。
- **不取整到 2 位**：任务书第 6、8 步没有取整要求，取整只损失精度。输出保留浮点原值，
  文件仍只有约 1.5 KB（上限 65536 字节）。
- **不依赖 `history` / `known` 的真实曲线做真值**：`history_hourly` 只到检查点，没有未来泄漏；
  真正用于建模的只有 `comparables`。
- **千金油兜底**：实测 4 个 case 的加权样本量 12.9~18.3（Kish 等效 18.6~22.9），
  远高于 5，兜底分支实际不触发，但保留以对齐规范。

## 验证结果（kit 4 个练习 case）

- `python test_format.py`：4 个 case 全部 PASS，格式合法、23 行、两次运行逐字节一致。
- 单次运行 0.17 s（上限 8 s）。
- 留一可比交叉验证：本模型 0.7226，房子基线 0.9296（kit 无练习发布真值，
  无法精确复现任务书所述 0.487 的交叉验证数字）。

## 环境坑（重要）

- DSH 沙箱下，`tempfile.mkdtemp()` 建出的 0700 目录连自己都写不进去，且**无法删除**。
  `test_format.py` 因此改用普通 `Path.mkdir` 建临时目录。
- 工作区外（如 `forecast\out`）对本进程只读：`PermissionError`。验证一律写工作区内目录。
- 仓库里残留三个删不掉的锁死目录 `.gta6-*`，已写进 `.gitignore`，不要提交。

## 命令

```sh
# 本地跑 kit 练习（Windows）
$env:MAKEFUN_INPUT_DIR = C:\Users\Administrator\makefun\forecast\gta6-twitch-ccu-forecast-01-kit\input
$env:MAKEFUN_OUTPUT_DIR = C:\Users\Administrator\makefun\gta6-forecast-entry\.local_out
python main.py

# 格式与确定性自检（会自行设输出目录）
python test_format.py
```
