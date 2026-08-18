import json
import tempfile
import unittest
from pathlib import Path

from lstest.scripts.adapter_guard import validate_adapter_source
from lstest.scripts.core import CaseResult, CaseSpec, TaskArtifacts, TaskSupervisor
from lstest.scripts.profile import DeviceProfile, RawLogRecord
from lstest.scripts.runtime import ScenarioRuntime
from lstest.scripts.timeline import ToolLogValidator


def profile_payload(profile_id: str, capture: str, marker: str, *, safety: bool = False) -> dict:
    rules = [
        {
            "rule_id": "result-rule",
            "event_type": "offline_result",
            "sources": {"sources": ["serial"]},
            "regex": rf"DATA=(?P<{capture}>[^\s]+)",
            "presentation_capture": capture,
            "stage": "command",
            "correlation": {},
            "mirror_policy": "distinct",
            "required_for": ["command"],
            "empty_placeholder": "",
            "fixtures": {"positive": [{"text": "DATA=raw-value", "source": "serial", "presentation": "raw-value"}], "negative": [{"text": "OTHER=raw-value", "source": "serial"}]},
        },
        {
            "rule_id": "player-rule",
            "event_type": "player_state",
            "sources": {"sources": ["serial"]},
            "regex": rf"STATE=(?P<marker>{marker})",
            "presentation_capture": "marker",
            "stage": "player",
            "correlation": {},
            "mirror_policy": "distinct",
            "required_for": ["player"],
            "empty_placeholder": "",
            "state_class": "active",
            "render_policy": "all",
            "fixtures": {"positive": [{"text": f"STATE={marker}", "source": "serial", "presentation": marker}], "negative": [{"text": "STATE=OTHER", "source": "serial"}]},
        },
    ]
    if safety:
        rules.append({
            "rule_id": "safety-rule", "event_type": "device_exception", "sources": {"sources": ["serial"]},
            "regex": "RISK=(?P<signal>STOP)", "presentation_capture": "signal", "stage": "guard",
            "correlation": {}, "mirror_policy": "distinct", "required_for": [], "empty_placeholder": "",
            "safety_eligible": True,
            "fixtures": {"positive": [{"text": "RISK=STOP", "source": "serial", "presentation": "STOP"}], "negative": [{"text": "RISK=GO", "source": "serial"}]},
        })
    payload = {"schema_version": 2, "profile_id": profile_id, "contract_mode": "formal", "ports": [], "commands": [], "event_rules": rules}
    if safety:
        payload["safety_stop"] = [{"event_rule_id": "safety-rule", "reason": "已验证的设备保护风险", "risk_category": "device", "fixtures": {"positive": [{"text": "RISK=STOP"}], "negative": [{"text": "RISK=GO"}]}}]
    return payload


class ProfileDrivenTests(unittest.TestCase):
    def _profile(self, root: Path, payload: dict) -> DeviceProfile:
        path = root / f"{payload['profile_id']}.json"
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return DeviceProfile.load(path)

    def test_two_profiles_keep_their_own_capture_and_player_marker(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for profile_id, capture, marker in (("first", "token_a", "alpha-marker"), ("second", "token_b", "beta-marker")):
                profile = self._profile(root, profile_payload(profile_id, capture, marker))
                artifacts = TaskArtifacts(root, profile_id)
                runtime = ScenarioRuntime(artifacts, profile=profile)
                case = CaseSpec("case", metadata={"timeline": {"required_facts": ["OFFLINE_ASR", "PLAYER"]}})
                runtime.freeze_cases([case], profile_version=profile.schema_version, profile_sha256=profile.sha256)
                runtime.open_case_window("case")
                runtime.submit_raw_record(RawLogRecord(text="DATA=raw-value", source="serial", sequence=1), case_id="case")
                runtime.submit_raw_record(RawLogRecord(text=f"STATE={marker}", source="serial", sequence=2), case_id="case")
                artifacts.record_case(CaseResult("case", "PASS"))
                summary = artifacts.finalize("PASS", "fixture")
                tool = artifacts.tool_log_path.read_text(encoding="utf-8")
                self.assertIn("OFFLINE_ASR: raw-value", tool)
                self.assertIn(f"PLAYER: {marker}", tool)
                self.assertEqual(summary["status"], "PASS")
                self.assertEqual(ToolLogValidator().validate_run(artifacts.run_dir), [])

    def test_profile_display_captures_render_raw_fields_in_declared_order(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            payload = profile_payload("multi-display", "intent", "marker")
            payload["event_rules"][0]["regex"] = r"keyword=(?P<keyword>[^\s]+) intent=(?P<intent>[^\s]+)"
            payload["event_rules"][0]["presentation_capture"] = "intent"
            payload["event_rules"][0]["display_captures"] = [
                {"tag_name": "keyword", "capture": "keyword"},
                {"tag_name": "intent", "capture": "intent"},
            ]
            payload["event_rules"][0]["fixtures"]["positive"] = [{
                "text": "keyword=xiao_t intent=open_ac", "source": "serial", "presentation": "open_ac",
                "display": {"keyword": "xiao_t", "intent": "open_ac"},
            }]
            profile = self._profile(root, payload)
            artifacts = TaskArtifacts(root, "multi-display")
            runtime = ScenarioRuntime(artifacts, profile=profile)
            case = CaseSpec("case", metadata={"timeline": {"required_facts": ["OFFLINE_ASR"]}})
            runtime.freeze_cases([case], profile_version=profile.schema_version, profile_sha256=profile.sha256)
            runtime.open_case_window("case")
            facts = runtime.submit_raw_record(
                RawLogRecord(text="keyword=xiao_t intent=open_ac", source="serial"), case_id="case",
            )
            self.assertEqual(facts[0].display_fields, (("keyword", "xiao_t"), ("intent", "open_ac")))
            artifacts.record_case(CaseResult("case", "PASS"))
            artifacts.finalize("PASS", "fixture")
            tool = artifacts.tool_log_path.read_text(encoding="utf-8")
            self.assertLess(tool.index("keyword: xiao_t"), tool.index("intent: open_ac"))
            self.assertNotIn("OFFLINE_ASR: open_ac", tool)

    def test_profile_rejects_missing_or_duplicate_display_capture(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            missing = profile_payload("bad-display-missing", "capture", "marker")
            missing["event_rules"][0]["display_captures"] = [{"tag_name": "keyword", "capture": "not_a_capture"}]
            with self.assertRaisesRegex(Exception, "display_captures"):
                self._profile(root, missing)
            duplicate = profile_payload("bad-display-duplicate", "capture", "marker")
            duplicate["event_rules"][0]["display_captures"] = [
                {"tag_name": "keyword", "capture": "capture"},
                {"tag_name": "keyword", "capture": "capture"},
            ]
            with self.assertRaisesRegex(Exception, "duplicates display tag"):
                self._profile(root, duplicate)

    def test_missing_required_profile_fact_has_empty_line_and_one_failed_result(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            profile = self._profile(root, profile_payload("missing-fact", "capture", "marker"))
            artifacts = TaskArtifacts(root, "missing-fact")
            runtime = ScenarioRuntime(artifacts, profile=profile)
            case = CaseSpec("case", metadata={"timeline": {"required_facts": ["OFFLINE_ASR"]}})
            runtime.freeze_cases([case], profile_version=profile.schema_version, profile_sha256=profile.sha256)
            runtime.open_case_window("case")
            artifacts.record_case(CaseResult("case", "PASS", reason="fixture"))
            summary = artifacts.finalize("PASS", "fixture")
            tool = artifacts.tool_log_path.read_text(encoding="utf-8")
            self.assertIn("OFFLINE_ASR:", tool)
            self.assertIn("缺少必需事实 OFFLINE_ASR", tool)
            self.assertEqual(tool.count("[RESULT] 本轮="), 1)
            self.assertEqual(summary["status"], "FAIL")

    def test_formal_runtime_rejects_legacy_fact_api(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            profile = self._profile(root, profile_payload("guard", "capture", "marker"))
            artifacts = TaskArtifacts(root, "guard")
            runtime = ScenarioRuntime(artifacts, profile=profile)
            with self.assertRaises(RuntimeError):
                runtime.record_recognition({}, source="offline")
            artifacts.finalize("PASS", "fixture")

    def test_safety_rule_stops_but_quality_stop_request_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            profile = self._profile(root, profile_payload("safety", "capture", "marker", safety=True))
            artifacts = TaskArtifacts(root, "safety")
            runtime = ScenarioRuntime(artifacts, profile=profile)
            self.assertFalse(artifacts.request_stop("INITIALIZATION_RECOVERY_FAILED", "普通恢复失败"))
            runtime.submit_raw_record(RawLogRecord(text="RISK=STOP", source="serial"), case_id="")
            self.assertEqual(artifacts.check_runtime(), "PROFILE_SAFETY_STOP")
            artifacts.finalize("STOPPED", "fixture")

    def test_task_supervisor_records_exception_and_returns_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            artifacts = TaskArtifacts(Path(directory), "supervisor")
            result = TaskSupervisor(artifacts).run("case", "play", lambda: (_ for _ in ()).throw(ValueError("boom")), fallback="continued")
            self.assertEqual(result, "continued")
            self.assertIsNone(artifacts.check_runtime())
            artifacts.finalize("PASS", "fixture")

    def test_adapter_guard_rejects_direct_log_and_legacy_api(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "adapter.py"
            path.write_text("artifacts.write_tool_log('x', 'y')\nruntime.record_recognition({}, source='x')\n", encoding="utf-8")
            self.assertEqual(len(validate_adapter_source(path)), 2)
