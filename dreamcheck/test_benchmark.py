"""Regression coverage for carrying saved run settings into Compare."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from .artifacts import write_json
from .benchmark import comparison_csv, load_evaluation_report, repair_plan, summarize
from .sorting import SORT_CASES


class ComparisonFixtures(unittest.TestCase):
    def test_report_save_retries_a_windows_sharing_error(self):
        real_replace = Path.replace
        blocked = PermissionError("sharing violation")
        blocked.winerror = 32
        attempts = []

        def replace(source, destination):
            attempts.append(source)
            if len(attempts) == 1:
                self.assertEqual(json.loads(destination.read_text(encoding="utf-8")), {"status": "running"})
                raise blocked
            return real_replace(source, destination)

        with tempfile.TemporaryDirectory() as root:
            destination = Path(root) / "report.json"
            write_json(destination, {"status": "running"})
            with patch.object(Path, "replace", replace):
                write_json(destination, {"status": "complete"})
            self.assertEqual(json.loads(destination.read_text(encoding="utf-8")), {"status": "complete"})
            self.assertEqual(list(Path(root).iterdir()), [destination])
        self.assertEqual(len(attempts), 2)

    def run_fixture(self, task, layout, **fields):
        return {"run_id": "a" * 12, "mode": "live", "verdict": "task_timeout", "task": task,
                "layout_id": layout, "prompt": "Do the task.", "model": "dreamlabs/dream-1-beta",
                "actions_per_chunk": 24, "seconds_limit": 90, **fields}

    def test_relay_repair_preserves_model_timing_and_start(self):
        run = self.run_fixture("relay", "relay-holdout-06", failure_phase="transfer")
        with patch("dreamcheck.benchmark.load_run", return_value=run):
            plan = repair_plan(run["run_id"])
            self.assertEqual(plan["layouts"][0], run["layout_id"])
            self.assertIn("flat plate", plan["instruction"])
            self.assertEqual(plan["seconds"], 90)
            self.assertEqual(plan["settings"]["A"], {**plan["settings"]["B"], "prompt": run["prompt"]})
            edited = repair_plan(run["run_id"], "Wait for the apple to settle.")
            self.assertEqual(edited["settings"]["B"]["prompt"], "Wait for the apple to settle.")
            with self.assertRaises(ValueError):
                repair_plan(run["run_id"], "a" * 401)

    def test_sorting_repair_keeps_fruit_and_bay_constant(self):
        layout = "sort-holdout-orange-far_bay-00"
        run = self.run_fixture("sorting", layout)
        run["verdict"] = "wrong_bay"
        with patch("dreamcheck.benchmark.load_run", return_value=run):
            plan = repair_plan(run["run_id"])
        self.assertEqual(plan["phase"], "wrong_bay")
        self.assertIn("amber bay", plan["instruction"])
        self.assertEqual(len(plan["layouts"]), 2)
        self.assertTrue(all(SORT_CASES[key][:2] == ("orange", "far_bay") for key in plan["layouts"]))

    def test_imported_comparison_replays_the_original_goal(self):
        run = self.run_fixture("imported", "imported-" + "b" * 12, region_goal={"object": "apple"},
                               scene_name="Parts bench")
        with patch("dreamcheck.benchmark.load_run", return_value=run):
            plan = repair_plan(run["run_id"], "Set the apple down in the middle of the tray.")
        self.assertEqual(plan["layouts"], [run["layout_id"]])
        self.assertEqual(plan["phase"], "goal_region")
        self.assertEqual(plan["settings"]["A"]["retry_of"], run["run_id"])
        self.assertEqual(plan["settings"]["B"]["retry_of"], run["run_id"])

    def test_physical_sorting_errors_are_scored_but_api_errors_are_excluded(self):
        report = summarize([
            {"runs": {"A": {"verdict": "passed"}, "B": {"verdict": "wrong_object_moved"}}},
            {"runs": {"A": {"verdict": "task_timeout"}, "B": {"verdict": "api_error"}}},
        ], "sorting")
        self.assertEqual(report["matched"], 1)
        self.assertEqual(report["A"]["passes"], 1)
        self.assertEqual(report["B"]["phases"]["wrong_object_moved"], 1)

    def test_running_evaluation_projects_into_compare_without_changing_report(self):
        report_id = "evaluation-20261122-000000-000001"
        result = {"task": "sorting", "prompt": "Sorting", "layouts": ["first", "second"], "repeats": 1,
                  "status": "running", "seconds_limit": 90, "simulated_seconds_per_request": 1.2,
                  "models": {"base": "dreamlabs/dream-1-beta", "fine_tuned": "dream-1-ft:test"},
                  "layout_prompts": {"first": "Apple into blue bay"},
                  "pairs": [{"layout": "first", "repeat": 1, "runs": {
                      "base": {"verdict": "passed", "run_id": "a" * 12},
                      "fine_tuned": {"verdict": "task_timeout", "run_id": "b" * 12,
                                     "failure_phase": "plate_transfer"}}}]}
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / f"{report_id}.json"
            original = json.dumps(result)
            path.write_text(original, encoding="utf-8")
            with patch("dreamcheck.training.OUTPUT", Path(root)), patch("dreamcheck.benchmark.load_run", return_value=None):
                report = load_evaluation_report(report_id)
                self.assertIsNone(load_evaluation_report("../" + report_id))
            self.assertEqual(path.read_text(encoding="utf-8"), original)
        self.assertEqual(report["summary"]["matched"], 1)
        self.assertEqual(report["summary"]["B"]["phases"]["transfer"], 1)
        self.assertEqual(report["pairs"][1]["runs"], {})
        self.assertFalse(report["pairs"][0]["runs"]["A"]["has_image"])
        self.assertIn("Apple into blue bay", comparison_csv(report))
