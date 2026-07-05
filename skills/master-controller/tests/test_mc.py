import argparse
import contextlib
import io
import importlib.util
import json
import shlex
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from unittest import mock
from pathlib import Path


MC_PATH = Path(__file__).resolve().parents[1] / "scripts" / "mc.py"
SPEC = importlib.util.spec_from_file_location("mc", MC_PATH)
mc = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = mc
SPEC.loader.exec_module(mc)
from mc_lib import runtime as mc_runtime  # noqa: E402
from mc_lib import tmux_adapter as mc_tmux_adapter  # noqa: E402
from mc_lib import commands as mc_commands  # noqa: E402
from mc_lib import runner as mc_runner  # noqa: E402


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


def configure_git_identity(repo):
    git(repo, "config", "user.email", "mc-test@example.invalid")
    git(repo, "config", "user.name", "MC Test")


def commit_all(repo, message="seed"):
    git(repo, "add", ".")
    git(repo, "commit", "-m", message)


def write_fake_harness(path):
    path.write_text(
        textwrap.dedent(
            """
            import json
            import os
            import subprocess
            import time
            from pathlib import Path

            artifact = Path(os.environ["MC_SLICE_ARTIFACT_DIR"])
            slice_id = os.environ["MC_SLICE_ID"]
            target = "README.md" if slice_id == "Slice 1" else "CHANGELOG.md"
            Path(target).write_text(f"{slice_id} completed\\n", encoding="utf-8")
            subprocess.run(["git", "add", target], check=True)
            subprocess.run(["git", "commit", "-m", f"Complete {slice_id}"], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            commit_hash = subprocess.run(["git", "rev-parse", "HEAD"], check=True, text=True, stdout=subprocess.PIPE).stdout.strip()
            (artifact / "validation-summary.md").write_text("PASS\\n", encoding="utf-8")
            (artifact / "drift-audit.md").write_text("PASS\\n", encoding="utf-8")
            (artifact / "code-review.md").write_text("PASS\\n", encoding="utf-8")
            result = {
                "schema_version": 1,
                "slice_id": slice_id,
                "status": "pass",
                "summary": f"{slice_id} done",
                "changed_files": [target],
                "validation": [{"command": "toy validation", "result": "pass", "notes": ""}],
                "drift_audit": {"verdict": "PASS", "path": "drift-audit.md"},
                "code_review": {"verdict": "PASS", "path": "code-review.md"},
                "commit": {"requested": True, "created": True, "hash": commit_hash},
                "next_action": "",
                "blockers": [],
            }
            (artifact / "orchestrator-result.json").write_text(json.dumps(result), encoding="utf-8")
            time.sleep(5)
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )


def write_no_result_harness(path):
    path.write_text(
        textwrap.dedent(
            """
            import time

            time.sleep(1)
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )


def write_repairable_then_pass_harness(path):
    path.write_text(
        textwrap.dedent(
            """
            import json
            import os
            import subprocess
            import time
            from pathlib import Path

            artifact = Path(os.environ["MC_SLICE_ARTIFACT_DIR"])
            marker = artifact / "repair-marker"
            slice_id = os.environ["MC_SLICE_ID"]
            if not marker.exists():
                marker.write_text("seen\\n", encoding="utf-8")
                (artifact / "orchestrator-result.json").write_text(json.dumps({
                    "schema_version": 1,
                    "slice_id": slice_id,
                    "status": "repairable",
                    "summary": "retry",
                    "changed_files": [],
                    "validation": [],
                    "drift_audit": {"verdict": "", "path": ""},
                    "code_review": {"verdict": "", "path": ""},
                    "commit": {"requested": False, "created": False, "hash": None},
                    "next_action": "retry",
                    "blockers": [],
                }), encoding="utf-8")
                time.sleep(1)
                raise SystemExit(0)

            Path("README.md").write_text("repaired\\n", encoding="utf-8")
            subprocess.run(["git", "add", "README.md"], check=True)
            subprocess.run(["git", "commit", "-m", "Complete repaired slice"], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            commit_hash = subprocess.run(["git", "rev-parse", "HEAD"], check=True, text=True, stdout=subprocess.PIPE).stdout.strip()
            (artifact / "validation-summary.md").write_text("PASS\\n", encoding="utf-8")
            (artifact / "drift-audit.md").write_text("PASS\\n", encoding="utf-8")
            (artifact / "code-review.md").write_text("PASS\\n", encoding="utf-8")
            (artifact / "orchestrator-result.json").write_text(json.dumps({
                "schema_version": 1,
                "slice_id": slice_id,
                "status": "pass",
                "summary": "repaired",
                "changed_files": ["README.md"],
                "validation": [{"command": "toy validation", "result": "pass", "notes": ""}],
                "drift_audit": {"verdict": "PASS", "path": "drift-audit.md"},
                "code_review": {"verdict": "PASS", "path": "code-review.md"},
                "commit": {"requested": True, "created": True, "hash": commit_hash},
                "next_action": "",
                "blockers": [],
            }), encoding="utf-8")
            time.sleep(1)
            """
        ).strip()
        + "\n",
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

    def test_previous_completed_head_returns_prior_completed_commit(self):
        state = {
            "slices": [
                {
                    "slice_id": "Slice 1",
                    "status": "pass",
                    "commit": {"hash": "a" * 40},
                },
                {
                    "slice_id": "Slice 2",
                    "status": "fail",
                    "commit": {"hash": "b" * 40},
                },
            ],
        }

        self.assertEqual(mc.previous_completed_head(state, "Slice 2"), "a" * 40)

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

    def test_prompt_rendering_includes_frozen_contract(self):
        state = self.init_run()
        run_json = (self.repo / ".ai-mc" / "current").resolve() / "run.json"
        plan_slice = mc.parse_plan(self.plan)[0]
        slice_artifact_dir = run_json.parent / "slices" / "slice-001"
        prompt = mc.render_orchestrator_prompt(state, plan_slice, slice_artifact_dir, run_json)
        self.assertIn("Selected slice: Slice 1 - First Slice", prompt)
        self.assertIn("Authorized surface:", prompt)
        self.assertIn("README.md", prompt)
        self.assertIn("orchestrator-result.json", prompt)
        self.assertIn(str(mc.skill_root() / "references" / "run-state-schema.md"), prompt)
        self.assertIn(str(mc.worker_jobs_path()), prompt)
        self.assertIn(str(slice_artifact_dir / "worker-runs"), prompt)
        self.assertIn(str(slice_artifact_dir / "tmp"), prompt)
        self.assertIn(str(slice_artifact_dir / "tool-homes"), prompt)
        self.assertIn(str(slice_artifact_dir / "copilot-home"), prompt)
        self.assertIn('run_dir="$(python3 ', prompt)
        self.assertIn('start --run-dir "$run_dir"', prompt)
        self.assertIn("worker-evidence.md", prompt)
        self.assertIn("Required worker tool(s) for this run: none configured for this run", prompt)

    def test_prompt_rendering_states_configured_worker_tools_authoritatively(self):
        state = self.init_run()
        run_json = (self.repo / ".ai-mc" / "current").resolve() / "run.json"
        plan_slice = mc.parse_plan(self.plan)[0]
        slice_artifact_dir = run_json.parent / "slices" / "slice-001"
        prompt = mc.render_orchestrator_prompt(state, plan_slice, slice_artifact_dir, run_json, ("codex",))
        self.assertIn("Required worker tool(s) for this run: codex", prompt)
        self.assertIn("authoritative for which worker tool(s) to use", prompt)

    def test_adapter_command_construction_exports_mc_environment(self):
        plan_slice = mc.parse_plan(self.plan)[0]
        adapter = mc.TmuxHarnessAdapter("codex", "python fake.py")
        command = adapter.build_shell_command(Path("/tmp/artifacts"), Path("/tmp/run.json"), self.plan, plan_slice)
        self.assertIn("AI_ORCHESTRATOR_ARTIFACT_ROOT=/tmp/artifacts/worker-runs", command)
        self.assertIn("COPILOT_HOME=/tmp/artifacts/copilot-home", command)
        self.assertIn("MC_RESULT_SCHEMA_PATH=", command)
        self.assertIn("MC_SLICE_ARTIFACT_DIR=/tmp/artifacts", command)
        self.assertIn("MC_SLICE_ID='Slice 1'", command)
        self.assertIn("MC_SLICE_TMP_DIR=/tmp/artifacts/tmp", command)
        self.assertIn("MC_TOOL_HOME_ROOT=/tmp/artifacts/tool-homes", command)
        self.assertIn("MC_WORKER_JOBS_PATH=", command)
        self.assertIn("TMPDIR=/tmp/artifacts/tmp", command)
        self.assertTrue(command.endswith("python fake.py"))

    def test_codex_profile_command_composes_worker_and_commit_requirements(self):
        self.prepare_committed_repo()
        state = self.init_run()
        command = mc.profile_command("codex", self.repo, state, ("copilot",))
        self.assertIn("codex --no-alt-screen -s workspace-write -a never", command)
        self.assertIn("sandbox_workspace_write.network_access=true", command)
        self.assertIn("--add-dir", command)
        self.assertIn(str(mc.git_access_path(self.repo)), command)

    def test_claude_profile_command_composes_model_and_session_id(self):
        self.prepare_committed_repo()
        state = self.init_run()
        command = mc.profile_command("claude", self.repo, state, ("codex",), "fixed-session-id", "sonnet")
        parts = shlex.split(command)
        self.assertEqual(parts, ["claude", "--permission-mode", "auto", "--model", "sonnet", "--session-id", "fixed-session-id"])

    def test_unsupported_profile_model_override_fails_closed(self):
        self.prepare_committed_repo()
        state = self.init_run()
        with self.assertRaisesRegex(mc.McError, "does not support"):
            mc.profile_command("codex", self.repo, state, (), harness_model="some-model")

    def test_harness_model_requires_profile_command(self):
        self.prepare_committed_repo()
        state = self.init_run()
        args = argparse.Namespace(harness_command=None, allow_profile_command=False, worker_tools="", harness_model="sonnet")
        with self.assertRaisesRegex(mc.McError, "only supported with --allow-profile-command"):
            mc.resolve_harness_command(args, self.repo, state)

    def test_copilot_profile_cannot_be_orchestrator(self):
        self.prepare_committed_repo()
        state = self.init_run()
        with self.assertRaisesRegex(mc.McError, "not approved for the orchestrator role"):
            mc.profile_command("copilot", self.repo, state, ())

    def test_codex_unattended_default_uses_no_alt_screen(self):
        adapter = mc.TmuxHarnessAdapter("codex", None, allow_unattended_default=True)
        self.assertEqual(adapter.command, "codex --no-alt-screen -s workspace-write -a never")

    def test_codex_readiness_wait_blocks_on_trust_prompt(self):
        adapter = mc.TmuxHarnessAdapter("codex", "codex")
        calls = [
            mc.CommandResult(0, "", ""),
            mc.CommandResult(0, "Do you trust the contents of this directory?", ""),
        ]
        with mock.patch.object(mc_tmux_adapter, "run_command", side_effect=calls), mock.patch.object(mc_tmux_adapter.time, "sleep"):
            with self.assertRaisesRegex(mc.McError, "trust prompt"):
                adapter.wait_until_prompt_ready("session")

    def test_codex_readiness_wait_accepts_ready_composer(self):
        adapter = mc.TmuxHarnessAdapter("codex", "codex")
        calls = [
            mc.CommandResult(0, "", ""),
            mc.CommandResult(0, "OpenAI Codex\n\n› Summarize recent commits", ""),
        ]
        with mock.patch.object(mc_tmux_adapter, "run_command", side_effect=calls), mock.patch.object(mc_tmux_adapter.time, "sleep") as sleep:
            adapter.wait_until_prompt_ready("session")
        sleep.assert_called()

    def test_adapter_detect_activity_reports_pane_changes(self):
        adapter = mc.TmuxHarnessAdapter("codex", "python fake.py")
        calls = [
            mc.CommandResult(0, "", ""),
            mc.CommandResult(0, "new pane text", ""),
        ]
        with mock.patch.object(mc_tmux_adapter, "run_command", side_effect=calls):
            activity = adapter.detect_activity("session", "old pane text")
        self.assertTrue(activity["running"])
        self.assertTrue(activity["active"])
        self.assertEqual(activity["capture"], "new pane text")

    def test_adapter_detect_activity_reports_stopped_session(self):
        adapter = mc.TmuxHarnessAdapter("codex", "python fake.py")
        with mock.patch.object(mc_tmux_adapter, "run_command", return_value=mc.CommandResult(1, "", "missing")):
            activity = adapter.detect_activity("session", "old pane text")
        self.assertFalse(activity["running"])
        self.assertFalse(activity["active"])
        self.assertEqual(activity["capture"], "")

    def write_gate_result(self, artifact, *, changed_files, validation_result="pass", drift="PASS", review="PASS", commit_hash=None):
        artifact.mkdir(parents=True, exist_ok=True)
        (artifact / "validation-summary.md").write_text("validation\n", encoding="utf-8")
        (artifact / "drift-audit.md").write_text("drift\n", encoding="utf-8")
        (artifact / "code-review.md").write_text("review\n", encoding="utf-8")
        result = {
            "schema_version": 1,
            "slice_id": "Slice 1",
            "status": "pass",
            "summary": "",
            "changed_files": changed_files,
            "validation": [] if validation_result is None else [{"command": "test", "result": validation_result, "notes": ""}],
            "drift_audit": {"verdict": drift, "path": "drift-audit.md"},
            "code_review": {"verdict": review, "path": "code-review.md"},
            "commit": {"requested": True, "created": bool(commit_hash), "hash": commit_hash},
            "next_action": "",
            "blockers": [],
        }
        (artifact / "orchestrator-result.json").write_text(json.dumps(result), encoding="utf-8")

    def write_gate_result_data(self, artifact, result):
        artifact.mkdir(parents=True, exist_ok=True)
        (artifact / "validation-summary.md").write_text("validation\n", encoding="utf-8")
        (artifact / "drift-audit.md").write_text("drift\n", encoding="utf-8")
        (artifact / "code-review.md").write_text("review\n", encoding="utf-8")
        (artifact / "orchestrator-result.json").write_text(json.dumps(result), encoding="utf-8")

    def prepare_committed_repo(self):
        configure_git_identity(self.repo)
        self.plan.write_text(self.plan.read_text(encoding="utf-8"), encoding="utf-8")
        (self.repo / "seed.txt").write_text("seed\n", encoding="utf-8")
        commit_all(self.repo)

    def test_gate_blocks_unauthorized_changed_file(self):
        self.prepare_committed_repo()
        before = git(self.repo, "rev-parse", "HEAD")
        (self.repo / "UNAUTHORIZED.md").write_text("bad\n", encoding="utf-8")
        git(self.repo, "add", "UNAUTHORIZED.md")
        git(self.repo, "commit", "-m", "Bad change")
        after = git(self.repo, "rev-parse", "HEAD")
        artifact = self.repo / ".ai-mc" / "runs" / "test" / "slices" / "slice-001"
        self.write_gate_result(artifact, changed_files=["UNAUTHORIZED.md"], commit_hash=after)
        state = self.init_run()
        decision = mc.verify_gate(self.repo, state, mc.parse_plan(self.plan)[0], artifact, before, after, mc.git_status_text(self.repo))
        self.assertEqual(decision.status, "fail")
        self.assertIn("unauthorized changed files", decision.reason)

    def test_gate_blocks_missing_validation(self):
        self.prepare_committed_repo()
        before = git(self.repo, "rev-parse", "HEAD")
        (self.repo / "README.md").write_text("ok\n", encoding="utf-8")
        git(self.repo, "add", "README.md")
        git(self.repo, "commit", "-m", "Good change")
        after = git(self.repo, "rev-parse", "HEAD")
        artifact = self.repo / ".ai-mc" / "runs" / "test" / "slices" / "slice-001"
        self.write_gate_result(artifact, changed_files=["README.md"], validation_result=None, commit_hash=after)
        state = self.init_run()
        decision = mc.verify_gate(self.repo, state, mc.parse_plan(self.plan)[0], artifact, before, after, mc.git_status_text(self.repo))
        self.assertEqual(decision.status, "fail")
        self.assertIn("validation evidence is missing", decision.reason)

    def test_gate_blocks_pass_with_risks_drift(self):
        self.prepare_committed_repo()
        before = git(self.repo, "rev-parse", "HEAD")
        (self.repo / "README.md").write_text("ok\n", encoding="utf-8")
        git(self.repo, "add", "README.md")
        git(self.repo, "commit", "-m", "Good change")
        after = git(self.repo, "rev-parse", "HEAD")
        artifact = self.repo / ".ai-mc" / "runs" / "test" / "slices" / "slice-001"
        self.write_gate_result(artifact, changed_files=["README.md"], drift="PASS WITH RISKS", commit_hash=after)
        state = self.init_run()
        decision = mc.verify_gate(self.repo, state, mc.parse_plan(self.plan)[0], artifact, before, after, mc.git_status_text(self.repo))
        self.assertEqual(decision.status, "needs-human")
        self.assertIn("drift audit verdict is not PASS", decision.reason)

    def test_gate_blocks_failed_review(self):
        self.prepare_committed_repo()
        before = git(self.repo, "rev-parse", "HEAD")
        (self.repo / "README.md").write_text("ok\n", encoding="utf-8")
        git(self.repo, "add", "README.md")
        git(self.repo, "commit", "-m", "Good change")
        after = git(self.repo, "rev-parse", "HEAD")
        artifact = self.repo / ".ai-mc" / "runs" / "test" / "slices" / "slice-001"
        self.write_gate_result(artifact, changed_files=["README.md"], review="FAIL", commit_hash=after)
        state = self.init_run()
        decision = mc.verify_gate(self.repo, state, mc.parse_plan(self.plan)[0], artifact, before, after, mc.git_status_text(self.repo))
        self.assertEqual(decision.status, "fail")
        self.assertIn("code review verdict is not PASS", decision.reason)

    def test_gate_fails_closed_on_malformed_audit_objects(self):
        self.prepare_committed_repo()
        before = git(self.repo, "rev-parse", "HEAD")
        (self.repo / "README.md").write_text("ok\n", encoding="utf-8")
        git(self.repo, "add", "README.md")
        git(self.repo, "commit", "-m", "Good change")
        after = git(self.repo, "rev-parse", "HEAD")
        artifact = self.repo / ".ai-mc" / "runs" / "test" / "slices" / "slice-001"
        self.write_gate_result_data(
            artifact,
            {
                "schema_version": 1,
                "slice_id": "Slice 1",
                "status": "pass",
                "summary": "",
                "changed_files": ["README.md"],
                "validation": [{"command": "test", "result": "pass", "notes": ""}],
                "drift_audit": None,
                "code_review": {"verdict": "PASS", "path": "code-review.md"},
                "commit": {"requested": True, "created": True, "hash": after},
                "next_action": "",
                "blockers": [],
            },
        )
        state = self.init_run()
        decision = mc.verify_gate(self.repo, state, mc.parse_plan(self.plan)[0], artifact, before, after, mc.git_status_text(self.repo))
        self.assertEqual(decision.status, "needs-human")
        self.assertIn("drift audit verdict is not PASS", decision.reason)

    def test_gate_accepts_repo_relative_artifact_paths(self):
        self.prepare_committed_repo()
        before = git(self.repo, "rev-parse", "HEAD")
        (self.repo / "README.md").write_text("ok\n", encoding="utf-8")
        git(self.repo, "add", "README.md")
        git(self.repo, "commit", "-m", "Good change")
        after = git(self.repo, "rev-parse", "HEAD")
        artifact = self.repo / ".ai-mc" / "runs" / "test" / "slices" / "slice-001"
        artifact.mkdir(parents=True, exist_ok=True)
        drift_path = artifact.relative_to(self.repo) / "drift-audit.md"
        review_path = artifact.relative_to(self.repo) / "code-review.md"
        self.write_gate_result_data(
            artifact,
            {
                "schema_version": 1,
                "slice_id": "Slice 1",
                "status": "pass",
                "summary": "",
                "changed_files": ["README.md"],
                "validation": [{"command": "test", "result": "pass", "notes": ""}],
                "drift_audit": {"verdict": "PASS", "path": str(drift_path)},
                "code_review": {"verdict": "PASS", "path": str(review_path)},
                "commit": {"requested": True, "created": True, "hash": after},
                "next_action": "",
                "blockers": [],
            },
        )
        state = self.init_run()
        decision = mc.verify_gate(self.repo, state, mc.parse_plan(self.plan)[0], artifact, before, after, mc.git_status_text(self.repo))
        self.assertEqual(decision.status, "pass")

    def test_gate_reconciles_fabricated_commit_hash_when_local_evidence_is_clear(self):
        self.prepare_committed_repo()
        before = git(self.repo, "rev-parse", "HEAD")
        (self.repo / "README.md").write_text("ok\n", encoding="utf-8")
        git(self.repo, "add", "README.md")
        git(self.repo, "commit", "-m", "Good change")
        after = git(self.repo, "rev-parse", "HEAD")
        self.assertTrue(after.startswith(git(self.repo, "rev-parse", "--short", "HEAD")))
        fabricated = git(self.repo, "rev-parse", "--short", "HEAD") + "0" * 33
        self.assertNotEqual(fabricated, after)
        artifact = self.repo / ".ai-mc" / "runs" / "test" / "slices" / "slice-001"
        self.write_gate_result(artifact, changed_files=["README.md"], commit_hash=fabricated)
        state = self.init_run()

        decision = mc.verify_gate(self.repo, state, mc.parse_plan(self.plan)[0], artifact, before, after, mc.git_status_text(self.repo))

        self.assertEqual(decision.status, "pass")
        self.assertIn("corrected reported commit hash", decision.reason)
        result = json.loads((artifact / "orchestrator-result.json").read_text(encoding="utf-8"))
        self.assertEqual(result["commit"]["hash"], after)
        self.assertTrue((artifact / "mc-reconciliation.json").exists())

    def test_gate_blocks_commit_hash_reconciliation_when_head_did_not_advance(self):
        self.prepare_committed_repo()
        before = git(self.repo, "rev-parse", "HEAD")
        artifact = self.repo / ".ai-mc" / "runs" / "test" / "slices" / "slice-001"
        self.write_gate_result(artifact, changed_files=[], commit_hash="0" * 40)
        state = self.init_run()

        decision = mc.verify_gate(self.repo, state, mc.parse_plan(self.plan)[0], artifact, before, before, mc.git_status_text(self.repo))

        self.assertEqual(decision.status, "fail")
        self.assertIn("did not advance HEAD", decision.reason)

    def test_capture_worker_runs_summary_records_status_files(self):
        artifact = self.repo / ".ai-mc" / "runs" / "test" / "slices" / "slice-001"
        worker_run = artifact / "worker-runs" / "workers-1"
        worker_run.mkdir(parents=True)
        (worker_run / "01-codex-check-status.json").write_text(
            json.dumps({"label": "01-codex-check", "state": "completed", "returncode": 0}),
            encoding="utf-8",
        )

        mc.capture_worker_runs_summary(artifact)

        summary = json.loads((artifact / "worker-runs-summary.json").read_text(encoding="utf-8"))
        self.assertEqual(summary["runs"][0]["workers"][0]["label"], "01-codex-check")

    def test_reconcile_repairs_failed_slice_after_commit_hash_evidence_mismatch(self):
        self.prepare_committed_repo()
        state = self.init_run()
        run_dir = (self.repo / ".ai-mc" / "current").resolve()
        before = git(self.repo, "rev-parse", "HEAD")
        (self.repo / "README.md").write_text("ok\n", encoding="utf-8")
        git(self.repo, "add", "README.md")
        git(self.repo, "commit", "-m", "Good change")
        after = git(self.repo, "rev-parse", "HEAD")
        artifact = run_dir / "slices" / "slice-001"
        self.write_gate_result(artifact, changed_files=["README.md"], commit_hash="0" * 40)
        state["slices"].append(
            {
                "slice_id": "Slice 1",
                "title": "First Slice",
                "status": "fail",
                "started_at": "2026-01-01T00:00:00Z",
                "artifact_dir": str(artifact.relative_to(self.repo.resolve())),
                "changed_files": ["README.md"],
                "validation": [{"command": "test", "result": "pass", "notes": ""}],
                "drift_audit": {"verdict": "PASS", "path": "drift-audit.md"},
                "code_review": {"verdict": "PASS", "path": "code-review.md"},
                "commit": {"requested": True, "created": True, "hash": "0" * 40},
                "next_action": "",
                "blockers": [],
                "gate_reason": "reported commit is not the current HEAD",
            }
        )
        state["status"] = "failed"
        state["stop_reason"] = "reported commit is not the current HEAD"
        (run_dir / "run.json").write_text(json.dumps(state), encoding="utf-8")

        args = argparse.Namespace(repo=str(self.repo), run="current")
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(mc.reconcile(args), 0)

        repaired = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
        self.assertEqual(repaired["slices"][0]["status"], "pass")
        self.assertEqual(repaired["slices"][0]["commit"]["hash"], after)
        self.assertEqual(repaired["status"], "partial")

    @unittest.skipUnless(shutil.which("tmux"), "tmux is required for runtime test")
    def test_run_next_executes_toy_harness_and_records_pass(self):
        self.prepare_committed_repo()
        harness = Path(self.tmp.name) / "fake_harness.py"
        write_fake_harness(harness)
        args = argparse.Namespace(repo=str(self.repo), plan=str(self.plan), harness="codex", worktree_root=None)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(mc.init_run(args), 0)
        run_args = argparse.Namespace(
            repo=str(self.repo),
            run="current",
            dry_run=False,
            timeout_seconds=10,
            poll_seconds=0.1,
            harness_command=f"{shlex.quote(sys.executable)} {shlex.quote(str(harness))}",
        )
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(mc.run_next(run_args), 0)
        state = json.loads(((self.repo / ".ai-mc" / "current").resolve() / "run.json").read_text(encoding="utf-8"))
        self.assertEqual(state["status"], "partial")
        self.assertEqual(state["slices"][0]["status"], "pass")
        self.assertEqual(state["slices"][0]["changed_files"], ["README.md"])
        self.assertTrue(((self.repo / ".ai-mc" / "current").resolve() / "slices" / "slice-001" / "pane-capture.txt").exists())
        self.assertTrue(((self.repo / ".ai-mc" / "current").resolve() / "slices" / "slice-001" / "pane-capture-live-latest.txt").exists())
        activity_path = (self.repo / ".ai-mc" / "current").resolve() / "slices" / "slice-001" / "activity-attempt-1.jsonl"
        self.assertTrue(activity_path.exists())
        activity = json.loads(activity_path.read_text(encoding="utf-8").splitlines()[0])
        self.assertEqual(set(activity), {"active", "checked_at", "running"})

    @unittest.skipUnless(shutil.which("tmux"), "tmux is required for runtime test")
    def test_run_remaining_completes_two_toy_slices(self):
        self.prepare_committed_repo()
        harness = Path(self.tmp.name) / "fake_harness.py"
        write_fake_harness(harness)
        args = argparse.Namespace(repo=str(self.repo), plan=str(self.plan), harness="codex", worktree_root=None)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(mc.init_run(args), 0)
        run_args = argparse.Namespace(
            repo=str(self.repo),
            run="current",
            scope="remaining",
            dry_run=False,
            timeout_seconds=10,
            poll_seconds=0.1,
            harness_command=f"{shlex.quote(sys.executable)} {shlex.quote(str(harness))}",
        )
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(mc.run_remaining(run_args), 0)
        state = json.loads(((self.repo / ".ai-mc" / "current").resolve() / "run.json").read_text(encoding="utf-8"))
        self.assertEqual(state["status"], "complete")
        self.assertEqual([entry["status"] for entry in state["slices"]], ["pass", "pass"])

    @unittest.skipUnless(shutil.which("tmux"), "tmux is required for runtime test")
    def test_run_next_blocks_when_session_exits_without_result(self):
        self.prepare_committed_repo()
        harness = Path(self.tmp.name) / "no_result_harness.py"
        write_no_result_harness(harness)
        args = argparse.Namespace(repo=str(self.repo), plan=str(self.plan), harness="codex", worktree_root=None)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(mc.init_run(args), 0)
        run_args = argparse.Namespace(
            repo=str(self.repo),
            run="current",
            dry_run=False,
            timeout_seconds=10,
            poll_seconds=0.1,
            harness_command=f"{shlex.quote(sys.executable)} {shlex.quote(str(harness))}",
        )
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(mc.run_next(run_args), 2)
        state = json.loads(((self.repo / ".ai-mc" / "current").resolve() / "run.json").read_text(encoding="utf-8"))
        self.assertEqual(state["status"], "blocked")
        self.assertIn("orchestrator result missing", state["stop_reason"])

    @unittest.skipUnless(shutil.which("tmux"), "tmux is required for runtime test")
    def test_run_next_retries_once_after_repairable_result(self):
        self.prepare_committed_repo()
        harness = Path(self.tmp.name) / "repairable_then_pass.py"
        write_repairable_then_pass_harness(harness)
        args = argparse.Namespace(repo=str(self.repo), plan=str(self.plan), harness="codex", worktree_root=None)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(mc.init_run(args), 0)
        run_args = argparse.Namespace(
            repo=str(self.repo),
            run="current",
            dry_run=False,
            timeout_seconds=10,
            poll_seconds=0.1,
            harness_command=f"{shlex.quote(sys.executable)} {shlex.quote(str(harness))}",
        )
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(mc.run_next(run_args), 0)
        run_dir = (self.repo / ".ai-mc" / "current").resolve()
        state = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
        self.assertEqual(state["slices"][0]["status"], "pass")
        self.assertTrue((run_dir / "slices" / "slice-001" / "activity-attempt-2.jsonl").exists())

    def test_run_remaining_stops_on_approval_needed_second_slice(self):
        write_plan(self.plan)
        text = self.plan.read_text(encoding="utf-8").replace(
            "Approval needed before implementation: no.\n\n### Validation Plan\n- Commands to run:\n  - git diff --check\n\n### Rollback Path\n- Revert CHANGELOG.md.",
            "Approval needed before implementation: yes.\n\n### Validation Plan\n- Commands to run:\n  - git diff --check\n\n### Rollback Path\n- Revert CHANGELOG.md.",
        )
        self.plan.write_text(text, encoding="utf-8")
        state = self.init_run()
        state["slices"].append({"slice_id": "Slice 1", "status": "pass"})
        run_json = (self.repo / ".ai-mc" / "current").resolve() / "run.json"
        run_json.write_text(json.dumps(state), encoding="utf-8")
        run_args = argparse.Namespace(
            repo=str(self.repo),
            run="current",
            scope="remaining",
            dry_run=False,
            timeout_seconds=1,
            poll_seconds=0.1,
            harness_command=None,
        )
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(mc.run_remaining(run_args), 2)
        stopped = json.loads(run_json.read_text(encoding="utf-8"))
        self.assertEqual(stopped["status"], "needs-human")
        self.assertIn("approval", stopped["stop_reason"])

    @unittest.skipUnless(shutil.which("tmux"), "tmux is required for preflight test")
    def test_preflight_passes_with_explicit_harness_command(self):
        self.prepare_committed_repo()
        self.init_run()
        args = argparse.Namespace(
            repo=str(self.repo),
            run="current",
            harness_command=sys.executable,
            worker_tools="",
            allow_profile_command=False,
        )
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(mc.preflight(args), 0)
        self.assertIn("Preflight passed.", output.getvalue())

    def test_stop_records_cancelled_state(self):
        self.init_run()
        args = argparse.Namespace(repo=str(self.repo), run="current", reason="test stop", harness_command=None)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(mc.stop(args), 0)
        state = json.loads(((self.repo / ".ai-mc" / "current").resolve() / "run.json").read_text(encoding="utf-8"))
        self.assertEqual(state["status"], "cancelled")
        self.assertEqual(state["stop_reason"], "test stop")

    def test_seed_worker_credentials_copies_codex_auth_when_requested(self):
        fake_codex_home = Path(self.tmp.name) / "fake-codex-home"
        fake_codex_home.mkdir()
        (fake_codex_home / "auth.json").write_text('{"token": "secret"}', encoding="utf-8")
        slice_artifact_dir = Path(self.tmp.name) / "slice-001"
        paths = mc.slice_paths(slice_artifact_dir)
        for path in paths.values():
            path.mkdir(parents=True, exist_ok=True)
        with mock.patch.dict("os.environ", {"CODEX_HOME": str(fake_codex_home)}):
            warnings = mc.seed_worker_credentials(paths, ("codex",), "claude")
        self.assertEqual(warnings, [])
        seeded = paths["codex_home"] / "auth.json"
        self.assertEqual(seeded.read_text(encoding="utf-8"), '{"token": "secret"}')
        self.assertEqual(seeded.stat().st_mode & 0o777, 0o600)

    def test_seed_worker_credentials_skips_when_tool_is_orchestrator_itself(self):
        fake_codex_home = Path(self.tmp.name) / "fake-codex-home"
        fake_codex_home.mkdir()
        (fake_codex_home / "auth.json").write_text('{"token": "secret"}', encoding="utf-8")
        slice_artifact_dir = Path(self.tmp.name) / "slice-001"
        paths = mc.slice_paths(slice_artifact_dir)
        for path in paths.values():
            path.mkdir(parents=True, exist_ok=True)
        with mock.patch.dict("os.environ", {"CODEX_HOME": str(fake_codex_home)}):
            warnings = mc.seed_worker_credentials(paths, ("codex",), "codex")
        self.assertEqual(warnings, [])
        self.assertFalse((paths["codex_home"] / "auth.json").exists())

    def test_seed_worker_credentials_warns_when_source_missing(self):
        fake_codex_home = Path(self.tmp.name) / "missing-codex-home"
        slice_artifact_dir = Path(self.tmp.name) / "slice-001"
        paths = mc.slice_paths(slice_artifact_dir)
        for path in paths.values():
            path.mkdir(parents=True, exist_ok=True)
        with mock.patch.dict("os.environ", {"CODEX_HOME": str(fake_codex_home)}):
            warnings = mc.seed_worker_credentials(paths, ("codex",), "claude")
        self.assertEqual(len(warnings), 1)
        self.assertIn("codex worker credential source not found", warnings[0])

    def test_slice_environment_isolates_worker_home_but_not_orchestrators_own(self):
        plan_slice = mc.parse_plan(self.plan)[0]
        artifact_dir = Path("/tmp/artifacts")
        run_json = Path("/tmp/run.json")
        claude_orchestrator_env = mc.slice_environment(artifact_dir, run_json, self.plan, plan_slice, "claude", ("codex",))
        self.assertEqual(claude_orchestrator_env["CODEX_HOME"], str(artifact_dir / "codex-home"))
        self.assertNotIn("CLAUDE_CONFIG_DIR", claude_orchestrator_env)

        codex_orchestrator_env = mc.slice_environment(artifact_dir, run_json, self.plan, plan_slice, "codex", ("codex",))
        self.assertNotIn("CODEX_HOME", codex_orchestrator_env)

        no_worker_env = mc.slice_environment(artifact_dir, run_json, self.plan, plan_slice)
        self.assertNotIn("CODEX_HOME", no_worker_env)
        self.assertNotIn("CLAUDE_CONFIG_DIR", no_worker_env)

    def test_profile_command_claude_appends_session_id(self):
        self.prepare_committed_repo()
        state = self.init_run()
        command = mc.profile_command("claude", self.repo, state, (), "fixed-session-id")
        self.assertIn("--session-id fixed-session-id", command)

    def test_capture_orchestrator_transcript_copies_existing_session_file(self):
        slice_artifact_dir = Path(self.tmp.name) / "slice-001"
        slice_artifact_dir.mkdir()
        session_id = "abc-123"
        expected_source = Path(self.tmp.name) / "claude-project" / f"{session_id}.jsonl"
        expected_source.parent.mkdir(parents=True)
        expected_source.write_text('{"type": "user"}\n', encoding="utf-8")
        with mock.patch.object(mc_runtime, "claude_orchestrator_transcript_path", return_value=expected_source):
            mc.capture_orchestrator_transcript("claude", self.repo, session_id, slice_artifact_dir)
        self.assertEqual(
            (slice_artifact_dir / "orchestrator-transcript.jsonl").read_text(encoding="utf-8"),
            '{"type": "user"}\n',
        )
        self.assertFalse((slice_artifact_dir / "orchestrator-transcript-note.txt").exists())

    def test_capture_orchestrator_transcript_notes_when_session_file_missing(self):
        slice_artifact_dir = Path(self.tmp.name) / "slice-001"
        slice_artifact_dir.mkdir()
        missing_source = Path(self.tmp.name) / "claude-project" / "missing.jsonl"
        with mock.patch.object(mc_runtime, "claude_orchestrator_transcript_path", return_value=missing_source):
            mc.capture_orchestrator_transcript("claude", self.repo, "some-id", slice_artifact_dir)
        self.assertFalse((slice_artifact_dir / "orchestrator-transcript.jsonl").exists())
        note = (slice_artifact_dir / "orchestrator-transcript-note.txt").read_text(encoding="utf-8")
        self.assertIn("orchestrator transcript not found", note)

    def test_capture_orchestrator_transcript_noop_for_non_claude_harness(self):
        slice_artifact_dir = Path(self.tmp.name) / "slice-001"
        slice_artifact_dir.mkdir()
        mc.capture_orchestrator_transcript("codex", self.repo, "some-id", slice_artifact_dir)
        self.assertFalse((slice_artifact_dir / "orchestrator-transcript.jsonl").exists())
        self.assertFalse((slice_artifact_dir / "orchestrator-transcript-note.txt").exists())

    def test_preflight_checks_worker_credential_source(self):
        self.prepare_committed_repo()
        args = argparse.Namespace(repo=str(self.repo), plan=str(self.plan), harness="claude", worktree_root=None)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(mc.init_run(args), 0)
        missing_codex_home = Path(self.tmp.name) / "missing-codex-home"
        preflight_args = argparse.Namespace(
            repo=str(self.repo),
            run="current",
            harness_command=None,
            worker_tools="codex",
            allow_profile_command=True,
        )
        output = io.StringIO()
        with mock.patch.dict("os.environ", {"CODEX_HOME": str(missing_codex_home)}):
            with contextlib.redirect_stdout(output):
                result = mc.preflight(preflight_args)
        self.assertEqual(result, 2)
        self.assertIn("codex worker credential source", output.getvalue())

    def test_preflight_skips_credential_check_when_worker_tool_is_orchestrator(self):
        self.prepare_committed_repo()
        state = self.init_run()
        missing_codex_home = Path(self.tmp.name) / "missing-codex-home"
        preflight_args = argparse.Namespace(
            repo=str(self.repo),
            run="current",
            harness_command=None,
            worker_tools="codex",
            allow_profile_command=True,
        )
        output = io.StringIO()
        with mock.patch.dict("os.environ", {"CODEX_HOME": str(missing_codex_home)}):
            with contextlib.redirect_stdout(output):
                mc.preflight(preflight_args)
        self.assertNotIn("codex worker credential source", output.getvalue())


    # --- Review fixes: fail-closed parsing -------------------------------

    def test_approval_free_text_blocks(self):
        for value in ["not yet decided", "none", "maybe later"]:
            write_plan(self.plan, approval=value)
            plan_slice = mc.parse_plan(self.plan)[0]
            self.assertIsNone(plan_slice.approval_needed, value)
            runnable, reasons = mc.eligibility(plan_slice)
            self.assertFalse(runnable, value)
            self.assertIn("approval-needed risk flag is missing or unclear", reasons)

    def test_approval_exact_no_runs(self):
        write_plan(self.plan, approval="no")
        self.assertFalse(mc.parse_plan(self.plan)[0].approval_needed)

    def test_authorized_files_ignores_stray_bullet(self):
        plan_slice = mc.PlanSlice(
            1,
            "t",
            "",
            {
                "Authorized Surface": (
                    "- Files allowed to change:\n"
                    "  - README.md\n"
                    "- Note: be careful in this area\n"
                    "- Tests allowed or expected to change: none."
                )
            },
        )
        self.assertEqual(plan_slice.authorized_files, ["README.md"])

    def test_is_authorized_path_glob_is_segment_aware(self):
        self.assertTrue(mc.is_authorized_path("a.md", ["*.md"]))
        self.assertFalse(mc.is_authorized_path("deep/a.md", ["*.md"]))
        self.assertTrue(mc.is_authorized_path("deep/a.md", ["**/*.md"]))
        self.assertTrue(mc.is_authorized_path("src/a.py", ["src/*.py"]))
        self.assertFalse(mc.is_authorized_path("src/deep/a.py", ["src/*.py"]))

    # --- Review fixes: fail-closed gate ----------------------------------

    def _commit_readme_change(self):
        before = git(self.repo, "rev-parse", "HEAD")
        (self.repo / "README.md").write_text("ok\n", encoding="utf-8")
        git(self.repo, "add", "README.md")
        git(self.repo, "commit", "-m", "Good change")
        return before, git(self.repo, "rev-parse", "HEAD")

    def test_gate_fails_closed_on_string_validation_entry(self):
        self.prepare_committed_repo()
        before, after = self._commit_readme_change()
        artifact = self.repo / ".ai-mc" / "runs" / "test" / "slices" / "slice-001"
        self.write_gate_result_data(
            artifact,
            {
                "schema_version": 1,
                "slice_id": "Slice 1",
                "status": "pass",
                "summary": "",
                "changed_files": ["README.md"],
                "validation": ["git diff --check ran fine"],
                "drift_audit": {"verdict": "PASS", "path": "drift-audit.md"},
                "code_review": {"verdict": "PASS", "path": "code-review.md"},
                "commit": {"requested": True, "created": True, "hash": after},
                "next_action": "",
                "blockers": [],
            },
        )
        state = self.init_run()
        decision = mc.verify_gate(self.repo, state, mc.parse_plan(self.plan)[0], artifact, before, after, mc.git_status_text(self.repo))
        self.assertEqual(decision.status, "fail")
        self.assertIn("validation entries are malformed", decision.reason)

    def test_gate_fails_closed_on_string_changed_files(self):
        self.prepare_committed_repo()
        before, after = self._commit_readme_change()
        artifact = self.repo / ".ai-mc" / "runs" / "test" / "slices" / "slice-001"
        self.write_gate_result_data(
            artifact,
            {
                "schema_version": 1,
                "slice_id": "Slice 1",
                "status": "pass",
                "summary": "",
                "changed_files": "README.md",
                "validation": [{"command": "test", "result": "pass", "notes": ""}],
                "drift_audit": {"verdict": "PASS", "path": "drift-audit.md"},
                "code_review": {"verdict": "PASS", "path": "code-review.md"},
                "commit": {"requested": True, "created": True, "hash": after},
                "next_action": "",
                "blockers": [],
            },
        )
        state = self.init_run()
        decision = mc.verify_gate(self.repo, state, mc.parse_plan(self.plan)[0], artifact, before, after, mc.git_status_text(self.repo))
        self.assertEqual(decision.status, "fail")
        self.assertIn("changed_files is malformed", decision.reason)

    def test_artifact_exists_requires_nonempty_in_tree_file(self):
        artifact = self.repo / ".ai-mc" / "runs" / "test" / "slices" / "slice-001"
        artifact.mkdir(parents=True)
        (artifact / "drift-audit.md").write_text("", encoding="utf-8")
        self.assertFalse(mc.artifact_exists(self.repo, artifact, {}, "drift_audit", "drift-audit.md"))
        (artifact / "drift-audit.md").write_text("verdict\n", encoding="utf-8")
        self.assertTrue(mc.artifact_exists(self.repo, artifact, {}, "drift_audit", "drift-audit.md"))
        artifact_relative = artifact.relative_to(self.repo) / "drift-audit.md"
        self.assertTrue(
            mc.artifact_exists(self.repo, artifact, {"drift_audit": {"path": str(artifact_relative)}}, "drift_audit", "drift-audit.md")
        )
        (self.repo / "README.md").write_text("not an audit artifact\n", encoding="utf-8")
        self.assertFalse(
            mc.artifact_exists(self.repo, artifact, {"drift_audit": {"path": "README.md"}}, "drift_audit", "drift-audit.md")
        )
        # An existing file outside the run must not satisfy the evidence check.
        self.assertFalse(
            mc.artifact_exists(self.repo, artifact, {"drift_audit": {"path": sys.executable}}, "drift_audit", "drift-audit.md")
        )

    # --- Review fixes: run integrity -------------------------------------

    def test_init_writes_self_ignoring_gitignore(self):
        self.init_run()
        gitignore = self.repo / ".ai-mc" / ".gitignore"
        self.assertTrue(gitignore.exists())
        self.assertEqual(gitignore.read_text(encoding="utf-8"), "*\n")

    def test_init_records_plan_digest(self):
        state = self.init_run()
        self.assertEqual(state["plan"]["sha256"], mc.plan_digest(self.plan))

    def test_verify_plan_unchanged_stops_on_edit(self):
        state = self.init_run()
        self.plan.write_text(self.plan.read_text(encoding="utf-8") + "\n<!-- edited -->\n", encoding="utf-8")
        with self.assertRaisesRegex(mc.McError, "plan file changed"):
            mc.verify_plan_unchanged(state, self.plan)

    def test_run_remaining_verifies_plan_before_completion_check(self):
        state = self.init_run()
        run_json = (self.repo / ".ai-mc" / "current").resolve() / "run.json"
        state["slices"].append({"slice_id": "Slice 1", "status": "pass"})
        run_json.write_text(json.dumps(state), encoding="utf-8")
        self.plan.write_text(self.plan.read_text(encoding="utf-8") + "\n<!-- edited -->\n", encoding="utf-8")
        args = argparse.Namespace(
            repo=str(self.repo),
            run="current",
            scope="remaining",
            dry_run=False,
            timeout_seconds=1,
            poll_seconds=0.1,
            harness_command=None,
        )
        with self.assertRaisesRegex(mc.McError, "plan file changed"):
            mc.run_remaining(args)

    def test_reconcile_verifies_plan_before_gate_recheck(self):
        state = self.init_run()
        run_dir = (self.repo / ".ai-mc" / "current").resolve()
        artifact = run_dir / "slices" / "slice-001"
        artifact.mkdir(parents=True)
        state["slices"].append(
            {
                "slice_id": "Slice 1",
                "title": "First Slice",
                "status": "fail",
                "started_at": "2026-01-01T00:00:00Z",
                "artifact_dir": str(artifact.relative_to(self.repo.resolve())),
                "before_head": None,
            }
        )
        (run_dir / "run.json").write_text(json.dumps(state), encoding="utf-8")
        self.plan.write_text(self.plan.read_text(encoding="utf-8") + "\n<!-- edited -->\n", encoding="utf-8")
        args = argparse.Namespace(repo=str(self.repo), run="current")
        with self.assertRaisesRegex(mc.McError, "plan file changed"):
            mc.reconcile(args)

    def test_init_rejects_duplicate_slice_numbers(self):
        dup = self.repo / "dup.md"
        dup.write_text("# Plan\n\n## Slice 1: A\n\n## Slice 1: B\n", encoding="utf-8")
        args = argparse.Namespace(repo=str(self.repo), plan=str(dup), harness="codex", worktree_root=None)
        with self.assertRaisesRegex(mc.McError, "duplicate slice numbers"):
            mc.init_run(args)

    def test_tool_homes_marked_sensitive(self):
        self.assertIn("tool-homes", mc.SENSITIVE_ARTIFACT_NAMES)

    def test_run_next_stops_when_branch_changed(self):
        self.prepare_committed_repo()
        self.init_run()
        git(self.repo, "checkout", "-b", "unexpected-branch")
        run_args = argparse.Namespace(
            repo=str(self.repo),
            run="current",
            dry_run=False,
            timeout_seconds=1,
            poll_seconds=0.1,
            harness_command=None,
        )
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(mc.run_next(run_args), 2)
        state = json.loads(((self.repo / ".ai-mc" / "current").resolve() / "run.json").read_text(encoding="utf-8"))
        self.assertEqual(state["status"], "needs-human")
        self.assertIn("branch changed since init", state["stop_reason"])

    def test_normalize_stop_status_maps_fail_and_unknown(self):
        self.assertEqual(mc.normalize_stop_status("fail"), "failed")
        self.assertEqual(mc.normalize_stop_status("weird"), "blocked")
        self.assertEqual(mc.normalize_stop_status("needs-human"), "needs-human")
        self.assertEqual(mc.normalize_stop_status("blocked"), "blocked")

    def test_slice_entry_records_before_head(self):
        gate = mc.GateDecision("pass", "ok", {"changed_files": []}, ())
        entry = mc.slice_entry_from_gate(self.repo, mc.parse_plan(self.plan)[0], self.repo / "art", "2026-01-01T00:00:00Z", gate, "abc123")
        self.assertEqual(entry["before_head"], "abc123")

    def test_reconcile_uses_recorded_before_head(self):
        self.prepare_committed_repo()
        state = self.init_run()
        run_dir = (self.repo / ".ai-mc" / "current").resolve()
        artifact = run_dir / "slices" / "slice-001"
        artifact.mkdir(parents=True)
        state["slices"].append(
            {
                "slice_id": "Slice 1",
                "title": "First Slice",
                "status": "fail",
                "started_at": "2026-01-01T00:00:00Z",
                "artifact_dir": str(artifact.relative_to(self.repo.resolve())),
                "before_head": "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef",
                "commit": {"requested": True, "created": True, "hash": "0" * 40},
            }
        )
        state["status"] = "failed"
        (run_dir / "run.json").write_text(json.dumps(state), encoding="utf-8")
        captured = {}

        def fake_gate(repo, run_state, plan_slice, art, before, after, status):
            captured["before"] = before
            return mc.GateDecision("fail", "still bad", {"changed_files": []}, ())

        args = argparse.Namespace(repo=str(self.repo), run="current")
        with mock.patch.object(mc_commands, "verify_gate", fake_gate):
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(mc.reconcile(args), 2)
        self.assertEqual(captured["before"], "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef")

    # --- Review fixes: harness readiness / launch parity -----------------

    def test_claude_readiness_blocks_on_trust_prompt(self):
        adapter = mc.TmuxHarnessAdapter("claude", "claude")
        calls = [
            mc.CommandResult(0, "", ""),  # session_exists
            mc.CommandResult(0, "Do you trust the files in this folder?", ""),  # pane capture
        ]
        with mock.patch.object(mc_tmux_adapter, "run_command", side_effect=calls), mock.patch.object(mc_tmux_adapter.time, "sleep"):
            with self.assertRaisesRegex(mc.McError, "trust prompt"):
                adapter._wait_claude_ready("session")

    @unittest.skipUnless(shutil.which("tmux"), "tmux is required for preflight parity test")
    def test_preflight_flags_bare_interactive_harness(self):
        self.prepare_committed_repo()
        self.init_run()
        args = argparse.Namespace(
            repo=str(self.repo),
            run="current",
            harness_command=None,
            worker_tools="",
            allow_profile_command=False,
            allow_unattended_default=False,
        )
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(mc.preflight(args), 2)
        self.assertIn("harness launch resolves", output.getvalue())
        self.assertIn("deadlock", output.getvalue())

    @unittest.skipUnless(shutil.which("tmux"), "tmux is required for runtime test")
    def test_run_next_blocks_on_unexpected_gate_exception(self):
        self.prepare_committed_repo()
        harness = Path(self.tmp.name) / "fake_harness.py"
        write_fake_harness(harness)
        args = argparse.Namespace(repo=str(self.repo), plan=str(self.plan), harness="codex", worktree_root=None)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(mc.init_run(args), 0)
        run_args = argparse.Namespace(
            repo=str(self.repo),
            run="current",
            dry_run=False,
            timeout_seconds=10,
            poll_seconds=0.1,
            harness_command=f"{shlex.quote(sys.executable)} {shlex.quote(str(harness))}",
        )
        with mock.patch.object(mc_runner, "verify_gate", side_effect=ValueError("boom")):
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(mc.run_next(run_args), 2)
        run_dir = (self.repo / ".ai-mc" / "current").resolve()
        state = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
        self.assertEqual(state["status"], "failed")
        self.assertIn("boom", state["stop_reason"])
        self.assertIsNone(state["current_slice"])

    @unittest.skipUnless(shutil.which("tmux"), "tmux is required for runtime test")
    def test_run_next_records_cancelled_state_on_keyboard_interrupt(self):
        self.prepare_committed_repo()
        harness = Path(self.tmp.name) / "fake_harness.py"
        write_fake_harness(harness)
        args = argparse.Namespace(repo=str(self.repo), plan=str(self.plan), harness="codex", worktree_root=None)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(mc.init_run(args), 0)
        run_args = argparse.Namespace(
            repo=str(self.repo),
            run="current",
            dry_run=False,
            timeout_seconds=10,
            poll_seconds=0.1,
            harness_command=f"{shlex.quote(sys.executable)} {shlex.quote(str(harness))}",
        )
        with mock.patch.object(mc_runner, "verify_gate", side_effect=KeyboardInterrupt):
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(mc.run_next(run_args), 2)
        run_dir = (self.repo / ".ai-mc" / "current").resolve()
        state = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
        self.assertEqual(state["status"], "cancelled")
        self.assertEqual(state["stop_reason"], "interrupted by user")
        self.assertIsNone(state["current_slice"])

    # --- Cross-skill dependency contract ---------------------------------

    def test_worker_jobs_module_exposes_claude_project_root(self):
        module = mc.worker_jobs_module()
        self.assertTrue(hasattr(module, "claude_project_root"))


if __name__ == "__main__":
    unittest.main()
