# 项目适配器边界（adapters/）

本目录存放**薄适配器**：只把项目原始日志行包装成 `RawLogRecord` 并交给运行时。
它不是第二个解析器，也不是第二个判定引擎。

## 1. 职责边界

| 允许做 | 禁止做 |
| --- | --- |
| 按行读取文本日志，保持原始行内容 | 匹配项目 marker、写正则、抽业务字段 |
| 从 profile 端口表取 `source` / `port` / `role` | 硬编码端口、波特率、角色名 |
| 从行首 `YYYY-MM-DDTHH:MM:SS` 前缀取 `observed_at` | 修改行内容、插入时间戳、格式化输出 |
| 记录物理行号 `cursor` 与递增 `sequence` | 生成 `ProfileFact`、`Fact`、判定结果 |
| 调用 `runtime.submit_raw_record(...)` | 写 `tool.log`、`results.csv`、任何结果文件 |
| 声明 case 顺序与播放动作（`case_plan.py`） | 在适配层声明期望结论、阈值、PASS/FAIL |

一句话：**适配器只搬运证据，不解释证据。** 解释在 `profiles/<project>/profile.json`，
判定在 `scripts/assertions.py`，归因与覆盖在 `scripts/attribution.py` / `scripts/coverage.py`，
期望值在 `cases/`。

## 2. 现有适配器

| 项目 | 拓扑 | 观测角色 | 说明 |
| --- | --- | --- | --- |
| `cskwb01` | `csk_ap_cp_wb01_control` | `ap` / `cp` / `asr` | AP + CP + WB01，另有 `control` 上下电口 |
| `venusws63` | `ap_wifi_control_no_cp` | `ap` / `upper`（`asr` 是 `upper` 的别名，同一物理口） | 无独立 CP；`control` 为电源/PA 口 |

`venusws63/case_plan.py` 只描述 **执行顺序 + 播放动作**（语料文件、重复次数、间隔），
反映 no-CP 拓扑：音频一律从主机注入设备麦克风，证据只从 `ap` / `upper` 两个观测口取。

## 3. 公开入口

每个项目的 `adapter.py` 只暴露两件事：

```python
def iter_records(
    path,                    # 一个串口日志文件
    *,
    profile=None,            # profile 路径 / DeviceProfile / 已加载的 payload；None 用本项目默认 profile
    role=None,               # 显式指定角色；缺省时从文件名推断
    default_source="serial", # 无法解析角色时的兜底 source
    default_phase="observe", # 记录上的默认阶段；profile 规则可覆盖
    start_cursor=1,
    start_sequence=1,
) -> Iterator[RawLogRecord]: ...

def submit(runtime, records, *, case_id=None) -> int: ...
```

`source` 取**解析出的角色名**（例如 `cp`），因为 profile 的事件规则用
`sources.sources: ["cp"]` 做作用域匹配；`default_source` 只在完全无法解析角色时生效。

角色解析顺序：

1. 显式 `role=`；
2. 文件名中出现的角色名（取最长匹配，例如 `serial_COM12_cp.log` → `cp`）；
3. 文件名中出现的端口号（唯一命中时）；
4. profile 只声明一个角色时直接用该角色。

无法解析就抛 `ValueError`，**不猜**。

`observed_at` 只取行首 `YYYY-MM-DDTHH:MM:SS`；没有前缀时为 `None`，不做任何补齐。
时间戳解析用逐字符判断实现，适配器里没有正则表达式。

## 4. 写一个新项目适配器

1. 先做日志发现，把 marker、捕获字段、端口、角色写进 `profiles/<project>/profile.json`；
   适配器不参与这一步。
2. 新建 `adapters/<project>/__init__.py`（可空）和 `adapters/<project>/adapter.py`，
   把本目录下任一现有适配器整体复制过去，只改 `PROJECT` 常量与 docstring。
   适配器刻意保持自包含：不共享基类，避免一个项目的特殊需求污染其它项目。
3. 需要声明执行顺序时再加 `case_plan.py`（只用标准库、只描述动作，不含期望结论）。
4. 在 `tests/test_adapters.py` 里加一个合成日志用例：记录数、顺序、`source/port/role`、
   时间戳解析、以及 `submit` 的转发顺序。
5. 运行：

```bash
cd <repos> && PYTHONPATH=<repos> <venv>/bin/python -m unittest lstest.tests.test_adapters -v
```

## 5. 证据引用

适配器不写 `evidence` 路径；运行时在收到带 `cursor` + `port` 的记录时会自行生成
`serial_logs/serial_<port>_<role>.log#<cursor>`。这样适配器永远不需要知道结果目录布局。
