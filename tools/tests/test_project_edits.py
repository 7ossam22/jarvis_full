#!/usr/bin/env python3
"""Write access to this project, and the gate that stands in front of it.

The property under test is one sentence: THE MODEL CANNOT APPROVE ITS OWN
PROPOSAL. Everything else here supports it — path containment so a proposal
cannot leave the project, decision parsing so ordinary conversation is not
mistaken for consent, and a staleness check so an approved diff is the diff
that lands.
"""
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from app import proposals                                       # noqa: E402
from app.connectors import project                              # noqa: E402
from app.connectors.registry import registry, LMSTUDIO, ANTHROPIC, GEMINI, OPENAI  # noqa: E402

SCRATCH = "notes/_test_project_edits.md"


class Containment(unittest.TestCase):
    def test_escapes_are_refused(self):
        for bad in ("../../etc/passwd", "../outside.py", "notes/../../x.py"):
            with self.assertRaises(proposals.ProposalError, msg=bad):
                proposals.safe_path(bad)

    def test_absolute_paths_are_refused_not_reinterpreted(self):
        """REGRESSION: stripping the leading slash turned /etc/passwd into
        <project>/etc/passwd — contained, but the caller believed it had
        addressed a different file entirely."""
        with self.assertRaises(proposals.ProposalError) as ctx:
            proposals.safe_path("/etc/passwd")
        self.assertIn("absolute", str(ctx.exception))

    def test_secrets_and_git_are_never_editable(self):
        for bad in ("config.json", ".git/config", ".git/HEAD"):
            with self.assertRaises(proposals.ProposalError, msg=bad):
                proposals.safe_path(bad)

    def test_ordinary_source_is_editable(self):
        for ok in ("app/controllers.py", "viewer/js/main.js", "README.md", "server.py"):
            _, rel = proposals.safe_path(ok)
            self.assertEqual(rel, ok)

    def test_gate_and_rules_are_flagged_sensitive(self):
        for path in ("app/proposals.py", "app/persona.py", "app/connectors/registry.py"):
            self.assertTrue(proposals.is_sensitive(path), path)
        self.assertFalse(proposals.is_sensitive("README.md"))


class Listing(unittest.TestCase):
    def test_virtualenvs_do_not_swamp_the_listing(self):
        """REGRESSION: a hand-written skip list named .venv and .venv-browser;
        a .venv-kokoro appeared later and buried the source tree under 2000
        files, so nothing under app/ was ever reached."""
        files = proposals.list_files(pattern="*", limit=5000)
        self.assertIn("app/controllers.py", files)
        self.assertEqual([f for f in files if ".venv" in f or "__pycache__" in f], [])

    def test_search_finds_real_code(self):
        hits = proposals.search("def handle_chat")
        self.assertTrue(any(h["path"] == "app/controllers.py" for h in hits))


class Staging(unittest.TestCase):
    def setUp(self):
        proposals.clear_all()
        self.target = os.path.join(proposals.ROOT, SCRATCH)
        self._cleanup()

    def tearDown(self):
        proposals.clear_all()
        self._cleanup()

    def _cleanup(self):
        for path in (self.target, self.target + ".jarvis-bak", self.target + ".jarvis-tmp"):
            if os.path.exists(path):
                os.remove(path)

    def test_proposing_writes_nothing(self):
        proposals.propose(SCRATCH, "because", new_content="hello\n")
        self.assertFalse(os.path.exists(self.target),
                         "a proposal must never touch the file system")

    def test_ambiguous_replacement_is_refused(self):
        with open(self.target, "w") as f:
            f.write("x = 1\nx = 1\n")
        with self.assertRaises(proposals.ProposalError) as ctx:
            proposals.propose(SCRATCH, "r", old_string="x = 1", new_string="x = 2")
        self.assertIn("appears 2 times", str(ctx.exception))

    def test_a_noop_change_is_refused(self):
        with open(self.target, "w") as f:
            f.write("same\n")
        with self.assertRaises(proposals.ProposalError):
            proposals.propose(SCRATCH, "r", new_content="same\n")

    def test_apply_writes_and_backs_up(self):
        with open(self.target, "w") as f:
            f.write("before\n")
        record = proposals.propose(SCRATCH, "r", new_content="after\n")
        proposals.apply(record["id"])
        self.assertEqual(open(self.target).read(), "after\n")
        self.assertEqual(open(self.target + ".jarvis-bak").read(), "before\n")

    def test_a_stale_proposal_is_refused(self):
        """The file moved after the diff was approved, so applying would
        silently revert whatever else changed it."""
        with open(self.target, "w") as f:
            f.write("v1\n")
        record = proposals.propose(SCRATCH, "r", new_content="v2\n")
        with open(self.target, "w") as f:
            f.write("someone else edited this\n")
        with self.assertRaises(proposals.ProposalError) as ctx:
            proposals.apply(record["id"])
        self.assertIn("changed since", str(ctx.exception))
        self.assertEqual(open(self.target).read(), "someone else edited this\n")

    def test_a_decided_change_cannot_be_applied_twice(self):
        record = proposals.propose(SCRATCH, "r", new_content="one\n")
        proposals.apply(record["id"])
        with self.assertRaises(proposals.ProposalError):
            proposals.apply(record["id"])

    def test_rejection_leaves_the_file_alone(self):
        with open(self.target, "w") as f:
            f.write("keep\n")
        record = proposals.propose(SCRATCH, "r", new_content="clobber\n")
        proposals.reject(record["id"])
        self.assertEqual(open(self.target).read(), "keep\n")


class TheGate(unittest.TestCase):
    """The one property: the model cannot approve its own proposal."""

    def test_no_tool_can_write_or_approve(self):
        exposed = {t["name"] for t in project.get_project_tools()}
        self.assertEqual(exposed, {
            "project_list_files", "project_search", "project_read_file",
            "project_propose_edit", "project_pending_changes"})

    def test_the_connector_never_references_the_write_path(self):
        import inspect
        source = inspect.getsource(project)
        for forbidden in ("proposals.apply", "proposals.reject", "clear_all"):
            self.assertNotIn(forbidden, source)

    def test_invented_approval_tools_are_refused(self):
        proposals.clear_all()
        target = os.path.join(proposals.ROOT, SCRATCH)
        proposals.propose(SCRATCH, "r", new_content="staged\n")
        for invented in ("project_apply", "project_approve", "proposals_apply",
                         "project_write_file", "apply_change"):
            result = registry.dispatch(invented, {"id": "1"}, LMSTUDIO, None)
            self.assertEqual(result["status"], "error", invented)
        self.assertFalse(os.path.exists(target))
        proposals.clear_all()

    def test_every_provider_gets_the_tools(self):
        """Granted to all backends deliberately: the gate is what makes this
        safe, not the provider list. The AGY and Claude CLI providers borrow
        the Gemini and Anthropic schemas, so they are covered by those two."""
        names = {t["name"] for t in project.get_project_tools()}
        for provider in (ANTHROPIC, GEMINI, LMSTUDIO, OPENAI):
            offered = set()
            for spec in registry.get_tools_for_provider(provider):
                offered.add(spec.get("name") or spec.get("function", {}).get("name"))
            self.assertTrue(names <= offered, f"{provider} is missing {names - offered}")


class Decisions(unittest.TestCase):
    def _action(self, text):
        decision = proposals.parse_decision(text)
        return decision["action"] if decision else None

    def test_explicit_instructions(self):
        for text in ("approve", "apply it", "go ahead", "do it", "approve #2", "approve all"):
            self.assertEqual(self._action(text), "approve", text)
        for text in ("reject", "discard change 3", "don't apply it",
                     "do not approve that", "never mind"):
            self.assertEqual(self._action(text), "reject", text)

    def test_bare_agreement_only_when_it_is_the_whole_message(self):
        """REGRESSION: "what a lovely day, sure is nice" matched "sure",
        approved a staged change and wrote the file to disk."""
        for text in ("yes", "sure", "ok", "yeah", "yes please"):
            self.assertEqual(self._action(text), "approve", text)
        for text in ("what a lovely day, sure is nice",
                     "yes I saw that film last night",
                     "ok so what do you see right now",
                     "I am sure the weather will hold",
                     "no idea what you mean by that"):
            self.assertIsNone(self._action(text), text)

    def test_refusal_beats_approval_in_the_same_sentence(self):
        for text in ("don't apply it", "do not approve that"):
            self.assertEqual(self._action(text), "reject", text)

    def test_unrelated_commands_are_not_decisions(self):
        for text in ("open novatek hcc", "play unfold on youtube",
                     "what do you see right now", "show me the LoL site"):
            self.assertIsNone(self._action(text), text)

    def test_ids_are_extracted(self):
        self.assertEqual(proposals.parse_decision("approve #7")["ids"], ["7"])
        self.assertEqual(proposals.parse_decision("approve all")["ids"], "all")
        self.assertIsNone(proposals.parse_decision("approve")["ids"])


class Router(unittest.TestCase):
    """The controller half: only a real decision, with something waiting,
    reaches the write path."""

    def setUp(self):
        from app import controllers
        self.controllers = controllers
        proposals.clear_all()
        self.target = os.path.join(proposals.ROOT, SCRATCH)
        if os.path.exists(self.target):
            os.remove(self.target)

    def tearDown(self):
        proposals.clear_all()
        for path in (self.target, self.target + ".jarvis-bak"):
            if os.path.exists(path):
                os.remove(path)

    def test_nothing_waiting_means_no_decision(self):
        self.assertIsNone(self.controllers._decide_staged_changes("approve", "s"))

    def test_approval_applies_and_short_circuits(self):
        proposals.propose(SCRATCH, "r", new_content="written\n")
        result = self.controllers._decide_staged_changes("approve", "s")
        self.assertIsNotNone(result, "an approval must not fall through to the model")
        self.assertEqual(open(self.target).read(), "written\n")

    def test_several_waiting_asks_instead_of_guessing(self):
        proposals.propose(SCRATCH, "a", new_content="a\n")
        proposals.propose("notes/_test_other.md", "b", new_content="b\n")
        payload, _ = self.controllers._decide_staged_changes("approve", "s")
        self.assertIn("Which one", payload["answer"])
        self.assertFalse(os.path.exists(self.target), "nothing may be written while ambiguous")
        self.assertEqual(len(proposals.pending(with_diff=False)), 2)

    def test_show_falls_through_to_the_model(self):
        proposals.propose(SCRATCH, "r", new_content="x\n")
        self.assertIsNone(self.controllers._decide_staged_changes("what does it change", "s"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
