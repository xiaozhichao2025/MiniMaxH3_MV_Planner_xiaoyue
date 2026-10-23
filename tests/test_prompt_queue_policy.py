from __future__ import annotations

import ast
import asyncio
import time
import unittest
from pathlib import Path
from typing import Any


SOURCE = Path(__file__).resolve().parents[1] / "server_api.py"


class PromptQueuePolicyTests(unittest.TestCase):
    def setUp(self):
        tree = ast.parse(SOURCE.read_text("utf-8"))
        function = next(node for node in tree.body
                        if isinstance(node, ast.FunctionDef)
                        and node.name == "_h3_prompt_job_conflict")
        module = ast.fix_missing_locations(ast.Module(body=[function], type_ignores=[]))
        self.jobs: dict[str, dict] = {}
        self.namespace = {
            "Any": Any,
            "_H3_PROMPT_JOBS": self.jobs,
            "continuation_predecessor": lambda plan, shot_id: (
                {"shot_id": plan["parents"][shot_id]}
                if shot_id in plan["parents"] else None
            ),
        }
        exec(compile(module, str(SOURCE), "exec"), self.namespace)
        self.conflict = self.namespace["_h3_prompt_job_conflict"]
        self.plan = {"parents": {"b": "a", "c": "b"}}

    def test_independent_shot_can_queue_behind_running_prompt(self):
        self.jobs["first"] = {"state": "running", "shot_ids": ["a"], "batch": False}
        self.assertEqual(self.conflict(self.plan, ["d"]), "")

    def test_same_shot_and_continuation_chain_cannot_use_stale_snapshot(self):
        self.jobs["first"] = {"state": "queued", "shot_ids": ["a"], "batch": False}
        self.assertIn("本镜头", self.conflict(self.plan, ["a"]))
        self.assertIn("续写链", self.conflict(self.plan, ["b"]))
        self.assertIn("续写链", self.conflict(self.plan, ["c"]))
        self.jobs["first"]["shot_ids"] = ["c"]
        self.assertIn("续写链", self.conflict(self.plan, ["a"]))

    def test_batch_is_exclusive_and_finished_jobs_do_not_block(self):
        self.jobs["first"] = {"state": "running", "shot_ids": ["a"], "batch": False}
        self.assertIn("批量", self.conflict(self.plan, ["d"], batch=True))
        self.jobs["first"]["batch"] = True
        self.assertIn("批量", self.conflict(self.plan, ["d"]))
        self.jobs["first"]["state"] = "completed"
        self.assertEqual(self.conflict(self.plan, ["a"], batch=True), "")

    def test_same_shot_id_in_another_project_is_independent(self):
        self.jobs["first"] = {
            "state": "queued", "project_id": "project-one",
            "shot_ids": ["a"], "batch": False,
        }
        self.plan["project"] = {"id": "project-two"}
        self.assertEqual(self.conflict(self.plan, ["a"]), "")

    def test_local_prompt_jobs_run_one_at_a_time(self):
        tree = ast.parse(SOURCE.read_text("utf-8"))
        function = next(node for node in tree.body
                        if isinstance(node, ast.AsyncFunctionDef)
                        and node.name == "_run_h3_prompt_job")
        module = ast.fix_missing_locations(ast.Module(body=[function], type_ignores=[]))

        async def exercise():
            first_started = asyncio.Event()
            release_first = asyncio.Event()
            order = []
            jobs = {"first": {"state": "queued"}, "second": {"state": "queued"}}

            async def execute(job_id, _data):
                order.append(job_id)
                if job_id == "first":
                    first_started.set()
                    await release_first.wait()
                jobs[job_id]["state"] = "completed"

            namespace = {
                "_H3_PROMPT_RUN_LOCK": asyncio.Lock(),
                "_H3_PROMPT_JOBS": jobs,
                "_execute_h3_prompt_job": execute,
                "time": time,
            }
            exec(compile(module, str(SOURCE), "exec"), namespace)
            run = namespace["_run_h3_prompt_job"]
            first = asyncio.create_task(run("first", {}))
            await first_started.wait()
            second = asyncio.create_task(run("second", {}))
            await asyncio.sleep(0)
            self.assertEqual(order, ["first"])
            self.assertEqual(jobs["second"]["state"], "queued")
            release_first.set()
            await asyncio.gather(first, second)
            self.assertEqual(order, ["first", "second"])
            self.assertEqual(jobs["second"]["state"], "completed")

        asyncio.run(exercise())


if __name__ == "__main__":
    unittest.main()
