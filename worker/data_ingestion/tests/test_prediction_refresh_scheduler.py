from __future__ import annotations

import unittest

from worker.data_ingestion.prediction_refresh_scheduler import run_prediction_refresh


class _FakeDb:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


class PredictionRefreshSchedulerTests(unittest.TestCase):
    def test_warning_step_is_visible_in_cycle_status(self):
        writes = []
        db = _FakeDb()

        report = run_prediction_refresh(
            operations=[
                (
                    "schedule",
                    lambda: {
                        "status": "warning",
                        "source_errors": ["source timeout"],
                    },
                ),
                ("snapshots", lambda: {"status": "ok", "created": 1}),
            ],
            db_factory=lambda: db,
            health_builder=lambda _: {
                "status": "ok",
                "summary": {},
                "checks": {},
                "warnings": [],
                "errors": [],
            },
            report_writer=lambda payload: writes.append(dict(payload)),
        )

        self.assertEqual(report["cycle_status"], "warning")
        self.assertEqual(report["status"], "warning")
        self.assertIn("source timeout", report["warnings"][0])
        self.assertTrue(db.closed)
        self.assertEqual(len(writes), 2)

    def test_failed_step_marks_cycle_failed_and_later_steps_continue(self):
        called = []

        def fail() -> dict:
            raise TimeoutError("schedule unavailable")

        report = run_prediction_refresh(
            operations=[
                ("schedule", fail),
                ("settlement", lambda: called.append("settlement") or {"status": "ok"}),
            ],
            db_factory=_FakeDb,
            health_builder=lambda _: {
                "status": "ok",
                "summary": {},
                "checks": {},
                "warnings": [],
                "errors": [],
            },
            report_writer=lambda _: None,
        )

        self.assertEqual(report["cycle_status"], "failed")
        self.assertEqual(report["status"], "failed")
        self.assertIn("TimeoutError", report["errors"][0])
        self.assertEqual(called, ["settlement"])

    def test_historical_health_warning_does_not_change_cycle_status(self):
        report = run_prediction_refresh(
            operations=[("snapshots", lambda: {"status": "ok", "created": 0})],
            db_factory=_FakeDb,
            health_builder=lambda _: {
                "status": "warning",
                "summary": {"historical_missing_final_snapshots": 3},
                "checks": {"historical_final_coverage": "warning"},
                "warnings": ["Historical gaps remain."],
                "errors": [],
            },
            report_writer=lambda _: None,
        )

        self.assertEqual(report["cycle_status"], "ok")
        self.assertEqual(report["status"], "warning")

    def test_final_report_write_failure_does_not_crash_scheduler(self):
        calls = []

        def writer(_: dict) -> None:
            calls.append(True)
            if len(calls) == 2:
                raise OSError("artifact volume unavailable")

        report = run_prediction_refresh(
            operations=[("snapshots", lambda: {"status": "ok"})],
            db_factory=_FakeDb,
            health_builder=lambda _: {
                "status": "ok",
                "summary": {},
                "checks": {},
                "warnings": [],
                "errors": [],
            },
            report_writer=writer,
        )

        self.assertEqual(report["cycle_status"], "failed")
        self.assertIn("write_final_refresh_report", report["errors"][-1])


if __name__ == "__main__":
    unittest.main()
