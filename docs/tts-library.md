# TTS 音频库消费边界（lstest）

- 状态：已实现（NOAH-66 第 5 轮）
- 更新时间：2026-11-12 17:08:26
- 关联：`result/tts-library-proposal.md`（1006 可复用 TTS 音频库规格 v1.0.0）
- 实现：`scripts/tts_library.py`（纯标准库）、`scripts/tts_library_cli.py`（独立入口）
- 测试：`tests/test_tts_library.py`（离线，仅用临时目录与假 `asset-tools` 脚本）

## 一句话

lstest 只是**薄消费适配器**：把测试用例的 TTS 需求转成 `ark.tts-spec.v1`，然后调用
`asset-tools tts-library`。`lstest` 先查库、命中就复制到任务目录；未命中交给
`tts-runtime` 合成。lstest **不拥有缓存**，也不参与共享库的写入。

## 责任边界

| 角色 | 职责 | 本轮边界 |
|---|---|---|
| `lstest` | 测试执行与用例编排 | **只做轻量消费适配器**：构造规格 + 调 `asset-tools` + 消费任务副本；不建缓存、不写库 |
| `asset-tools` | `tts-library` 子命令：key / lookup / fetch / publish / verify / index | **共享缓存核心**：缓存键、ACL、锁、outbox、query-back 全在这里 |
| `tts-runtime` | 唯一合成入口 `synth` / `batch` / `inspect` | **只在 cache miss 时合成**；不查库、不发布、不写 1006 |
| `audio-runtime` | 端点探测、播放、录制、convert/normalise | 只处理**任务副本**上的派生音频，不回写库原件 |
| `ark-kb` | 知识条目与检索 | 只存可搜索的资产索引（asset ID/SHA/作用域/位置），不存音频 |
| 1006 | `/srv/storage/ark/artifacts/audio/tts-library/` | **不可变 canonical**：发布后只读，只有 `asset-tools` 能写 |

一句话记忆：`asset-tools` 决定「要不要合成」，`tts-runtime` 只回答「怎么合成」，
`lstest` 只负责「按测试需求去查、去取、去用」。

## 唯一顺序：先查库、miss 才合成

```text
测试用例需要一段 TTS
 1) 生成完整 ark.tts-spec.v1（文本/音色/引擎/模型/参数/输出/归一化 profile）
 2) asset-tools tts-library lookup --spec spec.json
 3) 命中 → asset-tools tts-library fetch --spec spec.json \
             --dest assets/tts/original --task-dir <task>
      落到 <task>/assets/tts/original/<asset_id>/ 的**任务副本**
 4) 未命中 → tts-runtime synth 到 <task>/work/tts/<asset_id>/
 5) 质检（tts-runtime inspect 或 audio-runtime）
 6) asset-tools tts-library publish --staging <dir> --task-dir <task>
 7) 发布失败 → 写 receipts/outbox（pending）；任务可继续用本地结果，但不得声称库已更新
 8) 任务内二次处理 → 只写 <task>/work/audio 或 <task>/result/audio
```

**没有 lookup 结果，不允许开始合成**。lstest 的适配器只执行第 1～3 步，
第 4 步之后仍由测试任务调用相应仓库完成。

## 规格字段（`ark.tts-spec.v1`）

| 字段 | 说明 | 缺省/约束 |
|---|---|---|
| `schema` | 固定 `ark.tts-spec.v1` | 必填 |
| `text` | 原始文本（不做规范化） | 非空；规范化由 `asset-tools` 负责 |
| `language` | 语言 | 默认 `zh-CN` |
| `voice.speaker` | 音色名 | 与 `voice_ref_sha256` 互斥 |
| `voice.voice_ref_sha256` | 参考音频的 SHA-256 | 64 位小写十六进制；禁止写绝对路径 |
| `engine.name` | 引擎名 | 默认 `lstts` |
| `engine.model` | 模型 | 字符串引擎默认 `IndexTTS`（未知引擎为 `unknown`） |
| `engine.model_version` | 模型版本 | 默认 `unknown`；本项目需显式填真实版本 |
| `synthesis_params` | `rate` / `pitch` / `style` / `emotion` / `speed` | **固定键集**，未设置的键显式写 `null` |
| `output` | `format` / `sample_rate` / `channels` | 默认 `wav-pcm16` / `44100` / `2` |
| `normalization_profile` | 归一化档位 | 默认 `wav-pcm16-44100-stereo` |

注意：`build_spec()` 不计算 cache key、不做文本规范化、不应用默认值掩盖差异；
它只保证同一输入得到同一份规格。cache key 与 `asset_id` 由 `asset-tools` 计算，
调用方不得在 lstest 侧复算（禁止第二套键实现）。

## 命令行用法

```bash
# 直接由参数构造规格
python -u lstest/scripts/tts_library_cli.py spec \
  --text "今天天气不错" --voice 付小春 --engine lstts > spec.json

# 直接复用测试用例需求（未知键会以 warning 打到 stderr）
python -u lstest/scripts/tts_library_cli.py spec \
  --requirement cases/xxx/tts.json > spec.json

# 先查库
python -u lstest/scripts/tts_library_cli.py lookup --spec spec.json \
  --task-dir /home/zhsh/workspace/tasks/NOAH-66

# 命中后取件（复制，不硬链接）
python -u lstest/scripts/tts_library_cli.py fetch --spec spec.json \
  --task-dir /home/zhsh/workspace/tasks/NOAH-66 \
  --dest assets/tts/original
```

为什么不挂到 `scripts/lstest.py`：该文件在**模块导入阶段**就硬依赖 `lstts_core`
（`audio_synthesis` 顶部 import）。把查库子命令挂进去会让「合成之前」的步骤在
未安装合成依赖时完全不可用。独立入口只 import 纯标准库模块，既有子命令与参数不变。

## 退出码映射

| `asset-tools` 退出码 | lstest 适配器行为 | CLI 退出码 |
|---|---|---|
| 0 | 解析 stdout JSON，返回 dict | 0 |
| 2 | 抛 `UsageError`（参数/键/`--task-dir` 不合法） | 2 |
| 3 | 抛 `LibraryUnreachableError`（**库不可达 ≠ 资产不存在**） | 3 |
| 4 | 抛 `LibraryForbiddenError`（敏感内容、ACL、越权、只读写入） | 4 |
| 1 / 5 / 6 / 其他 | 抛 `LibraryError`（含 SHA 不符、传输失败） | 1 |

关键安全语义：**退出码 3 一律按「不可达」处理**，绝不输出「库里没有」。
宁可让任务走本地合成 + outbox 待重放，也不能把网络/挂载故障误判成 miss 并永久重复合成。

## 错误处理约定

- `lookup` 命中：`{"hit": true, "asset_id", "cache_key", "quality", "status", "manifest"}`。
- `lookup` 未命中：`{"hit": false, "status": "missing"}`（仅当 `asset-tools` 以 0 + `hit=false` 表达）。
- `fetch` 成功：在 lookup 结果上补 `audio_path` 与 `dest`（绝对路径只在任务侧使用）。
- `resolve`：lookup → 命中才 fetch；未命中返回 `hit=false`，由调用方接 `tts-runtime`。
- `publish`：必须给 `--task-dir`，暂存目录必须在任务目录内；失败写 outbox，不假装成功。
- 任何异常都只携带命令、退出码与截断后的 stderr/error 文本，**不打印凭据或 token**。

## lstest 绝对不做的事

1. **不持有共享凭据**：不读 `GITHUB_TOKEN`、`LSTTS_TTS_TOKEN` 或库侧 AK；不落配置文件。
2. **不写 1006**：库根只允许 `asset-tools tts-library` 写。适配器连目标路径都会先校验。
3. **不建本地缓存**：任务目录里的副本只是一次性输入，不是第二套库；不按 cache key 建索引。
4. **不计算 cache key / asset_id**：不 import `hashlib`，不复制规范化算法。
5. **不硬链接、不原地改库原件**：`fetch` 用复制；重采样/增益/降噪只写 `work/` 或 `result/`。
6. **不把不可达当缺失**：退出码 3 必须上抛 `LibraryUnreachableError`，不得静默走 miss。
7. **不在 `/tmp`、系统盘或库根做暂存**：所有规格临时文件都写在 `<task>/work/tts-library/`。
8. **不绕过 `tts-runtime` 合成**：缺少依赖时报告阻塞，不偷偷调用 `lstts_core` 直连。

## 从 `audio_synthesis.py` 迁移

现有 `lstest tts` / `tts-manifest` 的行为**保持不变**，继续走 `lstts_core` 直连，
用于没有共享库或需要临时合成的测试场景。新增测试建议按下面的顺序迁移：

1. 把用例里的文本/音色/参数写成需求映射，先用 `requirement_to_spec()` 生成规格；
2. 调 `adapter.resolve(spec)`：命中直接用 `audio_path`，未命中才调用 `tts-runtime synth`；
3. 需要固定采样率/声道时，先取库原件到任务副本，再由 `audio-runtime` 在副本上转换；
4. 合成产物若需入库，交给任务侧的 `asset-tools tts-library publish`，lstest 不代劳；
5. 既有脚本继续可用，不存在「必须一次性切换」的破坏性改动。

迁移时的两个常见坑：

- 旧代码把参考音频路径直接当 `voice` 传入：新规格只接受 `voice_ref_sha256`，
  请先对参考音频求 SHA-256 再传入，避免绝对路径进入缓存键。
- 旧流程的采样率/声道由 lstts 返回的 `container/codec/sample_rate/channels` 决定（常见
  16k/mono）：新规格默认 `wav-pcm16-44100-stereo`，如果设备需要
  16k/mono，请显式传 `output={"format": "wav-pcm16", "sample_rate": 16000, "channels": 1}`，
  并在取件后于任务副本上转换，不要修改库原件。

## 验证方式

```bash
cd <repo>
PYTHONPATH=.. python3 -m unittest discover -s tests -q   # 既有用例 + 新增用例
python3 ../ark-node/tools/time_lint.py .
python3 ../ark-node/tools/secret_scan.py .
```

测试完全离线：假 `asset-tools` 脚本写在临时目录，fetch 只写临时任务目录，
不会访问 `/srv/storage`，也不需要 `lstts_core`。
