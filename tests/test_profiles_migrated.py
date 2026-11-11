"""锁定从 Polaris marker 迁移而来的两个项目 profile（draft 合同）。

这些 profile 只做了 marker 字符串迁移，未采集真机日志，因此测试只验证
"能加载 + fixtures 自洽"，不暗示任何真机结论。
"""

import re
import unittest
from pathlib import Path

from lstest.scripts.profile import DeviceProfile, ProfileFixtureRunner, RawLogRecord


PROFILES_DIR = Path(__file__).resolve().parents[1] / "profiles"

# 每个项目的角色覆盖策略是项目数据，不允许引擎侧推断。
COVERAGE_POLICY = {
    "cskwb01": {"full": ["ap", "cp", "asr"], "minimum": ["ap"]},
    "venusws63": {"full": ["ap", "upper"], "minimum": ["ap"]},
}


class MigratedProfilesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.profiles = {
            name: DeviceProfile.load(PROFILES_DIR / name / "profile.json")
            for name in COVERAGE_POLICY
        }

    @staticmethod
    def _record(sample):
        item = dict(sample) if isinstance(sample, dict) else {"text": str(sample)}
        return RawLogRecord(
            text=str(item.get("text", "")),
            source=str(item.get("source", "")),
            port=str(item.get("port", "")),
            role=str(item.get("role", "")),
            phase=str(item.get("phase", "")),
        )

    def _facts_for(self, profile, sample, rule_id):
        return [fact for fact in profile.extract_facts(self._record(sample)) if fact.rule_id == rule_id]

    def test_profiles_load_as_draft_schema_two(self):
        for name, profile in self.profiles.items():
            with self.subTest(profile=name):
                self.assertEqual(profile.schema_version, 2)
                self.assertEqual(str(profile.payload.get("contract_mode")), "draft")
                self.assertFalse(profile.is_formal, "迁移期 profile 不能是 formal")

    def test_event_rules_have_unique_ids_and_named_captures(self):
        for name, profile in self.profiles.items():
            with self.subTest(profile=name):
                rules = profile.event_rules
                self.assertTrue(rules, "event_rules 不能为空")
                rule_ids = [str(rule["rule_id"]) for rule in rules]
                self.assertEqual(len(rule_ids), len(set(rule_ids)), "rule_id 必须唯一")
                for rule in rules:
                    pattern = re.compile(str(rule["regex"]), re.IGNORECASE)
                    self.assertTrue(pattern.groupindex, f"{rule['rule_id']} 缺少命名捕获组")
                    self.assertIn(
                        str(rule["presentation_capture"]), pattern.groupindex,
                        f"{rule['rule_id']} 的 presentation_capture 必须是命名捕获组",
                    )
                    self.assertTrue(
                        rule.get("fixtures", {}).get("positive"),
                        f"{rule['rule_id']} 至少需要一个 positive fixture",
                    )

    def test_fixtures_replay_through_profile_runner(self):
        for name, profile in self.profiles.items():
            with self.subTest(profile=name):
                results = ProfileFixtureRunner(profile).run()
                failed = [item.name for item in results if not item.passed]
                self.assertEqual(failed, [], "fixture runner 存在失败样本")

    def test_positive_samples_match_and_negatives_produce_no_fact(self):
        for name, profile in self.profiles.items():
            for rule in profile.event_rules:
                rule_id = str(rule["rule_id"])
                fixtures = rule.get("fixtures", {})
                for index, sample in enumerate(fixtures.get("positive", [])):
                    with self.subTest(profile=name, rule=rule_id, positive=index):
                        facts = self._facts_for(profile, sample, rule_id)
                        self.assertTrue(facts, f"{rule_id} positive fixture 未命中")
                        if isinstance(sample, dict) and sample.get("presentation") is not None:
                            self.assertEqual(
                                facts[0].presentation_value, str(sample["presentation"]),
                                f"{rule_id} 展示捕获与 fixture 期望不一致",
                            )
                for index, sample in enumerate(fixtures.get("negative", [])):
                    with self.subTest(profile=name, rule=rule_id, negative=index):
                        self.assertFalse(
                            self._facts_for(profile, sample, rule_id),
                            f"{rule_id} negative fixture 产生了事实",
                        )

    def test_provenance_and_coverage_policy(self):
        for name, profile in self.profiles.items():
            expected = COVERAGE_POLICY[name]
            with self.subTest(profile=name):
                provenance = profile.payload.get("provenance", {})
                self.assertIs(provenance.get("captured_device_logs"), False)
                self.assertIs(provenance.get("verified"), False)
                policy = profile.payload.get("coverage_policy", {})
                roles = policy.get("roles", {})
                self.assertEqual(roles.get("full"), expected["full"])
                self.assertEqual(roles.get("minimum"), expected["minimum"])
                self.assertEqual(policy.get("missing_non_minimum"), "COVERAGE_DEGRADED")
                self.assertEqual(policy.get("missing_minimum"), "BLOCKED")
                roles_declared = {str(item.get("role")) for item in profile.ports}
                self.assertTrue(set(expected["full"]) <= roles_declared)

    def test_venusws63_stays_cp_less(self):
        profile = self.profiles["venusws63"]
        roles = {str(item.get("role")) for item in profile.ports}
        self.assertNotIn("cp", roles)
        self.assertTrue({"ap", "upper", "asr"} <= roles)


if __name__ == "__main__":
    unittest.main()
