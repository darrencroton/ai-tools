import argparse
import contextlib
import io
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


MC_PATH = Path(__file__).resolve().parents[1] / "scripts" / "mc.py"
SPEC = importlib.util.spec_from_file_location("mc", MC_PATH)
mc = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = mc
SPEC.loader.exec_module(mc)


def git(repo, *args):
    result = subprocess.run(["git", "-C", str(repo), *args], check=False, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode != 0:
        raise AssertionError(result.stderr)
    return result.stdout.strip()


def write_plan(path, approval="no", include_authorized=True):
    authorized = """- Files allowed to change:
  - README.md
- Functions/classes/components allowed to change: none.
- Tests allowed or expected to change: none."""
    if not include_authorized:
        authorized = """- Functions/classes/components allowed to change: none.
- Tests allowed or expected to change: none."""
    path.write_text(
        f"""# Test Plan

## Slice 1: First Slice

### Intended Change
- Add docs.

### Acceptance Criteria
- Dry run identifies this slice.

### Authorized Surface
{authorized}

### Explicit Non-Goals
- Do not change runtime code.

### Risk Flags
- Risky surfaces touched: none.
- Approval needed before implementation: {approval}.

### Validation Plan
- Commands to run:
  - git diff --check

### Rollback Path
- Revert README.md.

## Slice 2: Second Slice

### Intended Change
- Add more docs.

### Acceptance Criteria
- Dry run identifies this slice after Slice 1.

### Authorized Surface
- Files allowed to change:
  - CHANGELOG.md
- Functions/classes/components allowed to change: none.
- Tests allowed or expected to change: none.

### Explicit Non-Goals
- Do not change runtime code.

### Risk Flags
- Risky surfaces touched: none.
- Approval needed before implementation: no.

### Validation Plan
- Commands to run:
  - git diff --check

### Rollback Path
- Revert CHANGELOG.md.
""",
        encoding="utf-8",
    )


class MasterControllerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name) / "repo"
        self.repo.mkdir()
        git(self.repo, "init")
        self.plan = self.repo / "plan.md"
        write_plan(self.plan)

    def tearDown(self):
        self.tmp.cleanup()

    def init_run(self):
        args = argparse.Namespace(repo=str(self.repo), plan=str(self.plan), harness="codex", worktree_root=None)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(mc.init_run(args), 0)
        current = self.repo / ".ai-mc" / "current"
        self.assertTrue(current.is_symlink())
        return json.loads((current.resolve() / "run.json").read_text(encoding="utf-8"))

    def test_run_state_creation(self):
        state = self.init_run()
        self.assertEqual(state["schema_version"], 1)
        self.assertEqual(state["repo_path"], str(self.repo.resolve()))
        self.assertEqual(state["plan_path"], str(self.plan.resolve()))
        self.assertEqual(state["harness"]["name"], "codex")
        self.assertEqual(state["plan"]["slice_count"], 2)

    def test_runnable_slice(self):
        slices = mc.parse_plan(self.plan)
        runnable, reasons = mc.eligibility(slices[0])
        self.assertTrue(runnable)
        self.assertEqual(reasons, [])
        self.assertEqual(slices[0].authorized_files, ["README.md"])

    def test_approval_needed_slice_blocks(self):
        write_plan(self.plan, approval="yes")
        slices = mc.parse_plan(self.plan)
        runnable, reasons = mc.eligibility(slices[0])
        self.assertFalse(runnable)
        self.assertIn("slice is approval-needed", reasons)

    def test_missing_authorized_surface_blocks(self):
        write_plan(self.plan, include_authorized=False)
        slices = mc.parse_plan(self.plan)
        runnable, reasons = mc.eligibility(slices[0])
        self.assertFalse(runnable)
        self.assertIn("authorized surface has no files allowed to change", reasons)

    def test_next_slice_skips_completed_state(self):
        self.init_run()
        run_json = (self.repo / ".ai-mc" / "current").resolve() / "run.json"
        state = json.loads(run_json.read_text(encoding="utf-8"))
        state["slices"].append({"slice_id": "Slice 1", "status": "pass"})
        run_json.write_text(json.dumps(state), encoding="utf-8")
        slices = mc.parse_plan(self.plan)
        self.assertEqual(mc.next_slice(slices, state).slice_id, "Slice 2")

    def test_final_slice_stops_before_future_work(self):
        self.plan.write_text(
            self.plan.read_text(encoding="utf-8")
            + """
## Future Work Outside This Plan

- Do not include this in Slice 2.

## Next Chat Prompt

Continue later.
""",
            encoding="utf-8",
        )
        slices = mc.parse_plan(self.plan)
        self.assertNotIn("Future Work", slices[-1].sections["Rollback Path"])
        self.assertNotIn("Next Chat Prompt", slices[-1].sections["Rollback Path"])


if __name__ == "__main__":
    unittest.main()
