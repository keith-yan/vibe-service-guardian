import re
import unittest
from importlib import resources


class WebAssetTests(unittest.TestCase):
    def test_dialog_close_buttons_never_submit_forms(self):
        html = resources.files("vsg").joinpath("web", "index.html").read_text(encoding="utf-8")
        script = resources.files("vsg").joinpath("web", "app.js").read_text(encoding="utf-8")
        buttons = re.findall(r"<button\b[^>]*data-close-dialog[^>]*>", html)
        self.assertGreaterEqual(len(buttons), 5)
        self.assertTrue(all('type="button"' in button for button in buttons))
        self.assertIn("[data-close-dialog]", script)

    def test_model_planner_assets_are_present(self):
        html = resources.files("vsg").joinpath("web", "index.html").read_text(encoding="utf-8")
        script = resources.files("vsg").joinpath("web", "app.js").read_text(encoding="utf-8")
        for element_id in (
            "model-planner-view",
            "planner-form",
            "ceiling-physical",
            "ceiling-usable",
            "ceiling-sla",
            "candidate-body",
            "benchmark-dialog",
            "count-model-runtime",
        ):
            self.assertIn(f'id="{element_id}"', html)
        self.assertIn("/api/model-planner/estimate", script)
        self.assertIn("/api/model-planner/benchmark", script)

    def test_bilingual_assets_and_language_toggle_are_present(self):
        html = resources.files("vsg").joinpath("web", "index.html").read_text(encoding="utf-8")
        i18n = resources.files("vsg").joinpath("web", "i18n.js").read_text(encoding="utf-8")
        self.assertIn('id="language-toggle"', html)
        self.assertIn('/assets/i18n.js', html)
        self.assertIn('vsg.locale', i18n)
        self.assertIn('navigator.languages', i18n)
        self.assertIn('Service Monitor', i18n)
        self.assertIn('["不设置", "Not set"]', i18n)

    def test_runtime_checkup_assets_and_explicit_actions_are_present(self):
        html = resources.files("vsg").joinpath("web", "index.html").read_text(encoding="utf-8")
        script = resources.files("vsg").joinpath("web", "app.js").read_text(encoding="utf-8")
        for element_id in (
            "health-view",
            "health-overall",
            "live-gpus",
            "runtime-health-body",
            "health-findings",
            "service-benchmark-dialog",
            "diagnostic-dialog",
            "snapshot-form",
            "restore-dialog",
        ):
            self.assertIn(f'id="{element_id}"', html)
        for endpoint in (
            "/api/service/benchmark",
            "/api/diagnostics/",
            "/api/snapshots/create",
            "/api/snapshots/restore",
        ):
            self.assertIn(endpoint, script)

    def test_optimization_advisor_and_log_monitor_assets_are_present(self):
        html = resources.files("vsg").joinpath("web", "index.html").read_text(encoding="utf-8")
        script = resources.files("vsg").joinpath("web", "app.js").read_text(encoding="utf-8")
        for element_id in (
            "advisor-view",
            "advisor-form",
            "engine-top3",
            "advisor-recommendations",
            "benchmark-comparison",
            "log-watch-form",
            "log-watches",
            "log-event-body",
            "setting-log-retention",
        ):
            self.assertIn(f'id="{element_id}"', html)
        for endpoint in (
            "/api/advisor/status",
            "/api/advisor/evaluate",
            "/api/log-monitor/watch",
            "/api/log-monitor/unwatch",
        ):
            self.assertIn(endpoint, script)
        self.assertIn("WATCH ${service.process.pid}", script)

    def test_v08_operations_workspace_and_confirmations_are_present(self):
        html = resources.files("vsg").joinpath("web", "index.html").read_text(encoding="utf-8")
        script = resources.files("vsg").joinpath("web", "app.js").read_text(encoding="utf-8")
        for element_id in (
            "operations-view",
            "model-inventory-form",
            "model-inventory-root",
            "model-inventory-body",
            "network-topology-list",
            "attribution-rule-list",
            "timeline-list",
            "history-clear-form",
            "attribution-dialog",
            "confirmation-dialog",
        ):
            self.assertIn(f'id="{element_id}"', html)
        for endpoint in (
            "/api/model-inventory/scan",
            "/api/network-topology",
            "/api/attribution/rules",
            "/api/incidents",
            "/api/history/clear",
        ):
            self.assertIn(endpoint, script)
        self.assertIn("SCAN MODELS", html)
        self.assertIn("CLEAR HISTORY", html)
        self.assertNotIn("window.prompt", script)

    def test_v081_relationship_stop_and_workload_matrix_assets_are_present(self):
        html = resources.files("vsg").joinpath("web", "index.html").read_text(encoding="utf-8")
        script = resources.files("vsg").joinpath("web", "app.js").read_text(encoding="utf-8")
        for element_id in (
            "relationship-list",
            "stop-assessment",
            "stop-verification",
            "planner-calibration-summary",
            "workload-matrix-dialog",
            "workload-matrix-plan",
            "workload-matrix-status",
            "workload-matrix-cancel",
        ):
            self.assertIn(f'id="{element_id}"', html)
        for endpoint in (
            "/api/service/stop-assessment",
            "/api/benchmark-matrix/preview",
            "/api/benchmark-matrix/start",
            "/api/benchmark-matrix/cancel",
            "/api/benchmark-matrix/status",
        ):
            self.assertIn(endpoint, script)
        self.assertIn("plan.confirmation", script)
        self.assertIn("prediction_error", script)

    def test_v084_observation_profiles_and_project_runtime_assets_are_present(self):
        html = resources.files("vsg").joinpath("web", "index.html").read_text(encoding="utf-8")
        script = resources.files("vsg").joinpath("web", "app.js").read_text(encoding="utf-8")
        i18n = resources.files("vsg").joinpath("web", "i18n.js").read_text(encoding="utf-8")
        for element_id in (
            "stop-observation-bar",
            "stop-observation-minutes",
            "attribution-lifecycle-label",
            "attribution-inherit",
            "measured-profile-list",
            "planner-calibration-service",
            "project-runtime-body",
        ):
            self.assertIn(f'id="{element_id}"', html)
        for endpoint in (
            "/api/stop-observations",
            "/api/calibration-profiles/status",
            "/api/calibration-profiles/delete",
            "/api/service/lifecycle-label/clear",
        ):
            self.assertIn(endpoint, script)
        self.assertIn("Port state is awaiting evidence", i18n)
        self.assertIn("Multiple models are loaded", i18n)

    def test_local_impact_evidence_requires_explicit_export(self):
        html = resources.files("vsg").joinpath("web", "index.html").read_text(encoding="utf-8")
        script = resources.files("vsg").joinpath("web", "app.js").read_text(encoding="utf-8")
        for element_id in (
            "impact-report-button",
            "impact-report-dialog",
            "impact-report-form",
            "impact-report-summary",
            "impact-export-confirmation",
        ):
            self.assertIn(f'id="{element_id}"', html)
        for endpoint in (
            "/api/impact",
            "/api/impact/feedback",
            "/api/impact/export",
        ):
            self.assertIn(endpoint, script)
        self.assertIn("EXPORT REPORT", html)
        self.assertIn("data-impact-outcome", script)
        self.assertIn('value="impact_feedback"', html)

    def test_daily_attention_and_actionable_service_controls_are_present(self):
        html = resources.files("vsg").joinpath("web", "index.html").read_text(encoding="utf-8")
        script = resources.files("vsg").joinpath("web", "app.js").read_text(encoding="utf-8")
        for element_id in (
            "attention-list",
            "runtime-glance",
            "attention-show-all",
            "count-focus",
        ):
            self.assertIn(f'id="{element_id}"', html)
        self.assertIn('source: "focus"', script)
        self.assertIn("建议路径", script)
        self.assertIn("READ-ONLY GUIDANCE", script)
        self.assertNotIn('data-action="stop" title="停止进程树" disabled', script)


if __name__ == "__main__":
    unittest.main()
