# 项目适配 Skill 模板

```text
<project-skill>/
  SKILL.md
  agents/openai.yaml
  config/<project>-profile.json
  fixtures/<project>-profile-fixtures.json
  scripts/record_adapter.py
  scripts/scenario.py
  corpus/cases.csv
```

`record_adapter.py` 只能持续采集并提交 `RawLogRecord`。`scenario.py` 只定义 case、播放动作、等待窗口与项目 oracle。profile 和 fixtures 由真实日志发现流程生成；不得把项目 marker 复制进 `lstest`。

验收命令占位：

```powershell
python -m pytest -q tests
python -u <project-skill>/scripts/run.py --preflight
python -u <project-skill>/scripts/run.py --replay fixtures/<project>-profile-fixtures.json
```
