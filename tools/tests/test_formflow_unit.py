#!/usr/bin/env python3
"""Unit tests for app/formflow.py — the deterministic pre-LLM router that
keeps Novatek form submission away from every AI provider.

Run with plain stdlib Python:
    python3 tools/tests/test_formflow_unit.py -v
"""
import os
import sys
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from app import formflow  # noqa: E402


class StubCfg:
    def get(self, key, default=None):
        return {"persona.address_term": "sir",
                "retrieval.max_history_turns": 6}.get(key, default)


class TestDetectIntent(unittest.TestCase):
    def test_form_commands(self):
        for msg in ("fill the current form", "Fill current form",
                    "fill form in Novatek", "complete the form",
                    "submit this form please", "answer the whole form"):
            self.assertEqual(formflow.detect_intent(msg), ("form", {}), msg)

    def test_visit_commands(self):
        for msg in ("complete the visit", "complete current visit",
                    "finish this visit", "complete the screening visit",
                    "end the visit"):
            self.assertEqual(formflow.detect_intent(msg),
                             ("visit", {"max_visits": 1}), msg)

    def test_takeover_commands(self):
        self.assertEqual(formflow.detect_intent("take over this participant"),
                         ("takeover", {"max_visits": 5}))
        self.assertEqual(formflow.detect_intent("take over participant 100-013-463"),
                         ("takeover", {"max_visits": 5}))
        self.assertEqual(formflow.detect_intent("take over the participant, 9 visits"),
                         ("takeover", {"max_visits": 9}))
        self.assertEqual(formflow.detect_intent("complete all visits for this participant"),
                         ("takeover", {"max_visits": 20}))
        self.assertEqual(formflow.detect_intent("run the participant to the end"),
                         ("takeover", {"max_visits": 20}))

    def test_takeover_beats_visit_and_form(self):
        intent, _ = formflow.detect_intent("take over the participant and complete all visits")
        self.assertEqual(intent, "takeover")

    def test_command_synonyms(self):
        self.assertEqual(formflow.detect_intent("autofill the form"), ("form", {}))
        self.assertEqual(formflow.detect_intent("do the form"), ("form", {}))
        self.assertEqual(formflow.detect_intent("please fill the form"), ("form", {}))
        self.assertEqual(formflow.detect_intent("akira, take over the participant"),
                         ("takeover", {"max_visits": 5}))
        self.assertEqual(formflow.detect_intent("do the visit"),
                         ("visit", {"max_visits": 1}))
        self.assertEqual(formflow.detect_intent("submit the visit"),
                         ("visit", {"max_visits": 1}))

    def test_questions_are_not_commands(self):
        for msg in ("how do I fill the form?", "what is a consent form",
                    "can you explain how the visit works",
                    "why did the form submission fail",
                    "is the form complete?",
                    "did you finish the visit",
                    "if you were to fill the form, what would you enter?"):
            self.assertIsNone(formflow.detect_intent(msg), msg)

    def test_negations_and_deferrals_never_fire(self):
        for msg in ("don't fill the form yet",
                    "do not complete the visit until I say so",
                    "never take over the participant without asking",
                    "stop filling the form",
                    "wait before you complete the visit",
                    "fill the form later",
                    "complete the visit tomorrow",
                    "let's do all the visits next week",
                    "we might finish all visits by friday",
                    "remind me to fill the form"):
            self.assertIsNone(formflow.detect_intent(msg), msg)

    def test_third_party_and_non_imperative_never_fire(self):
        for msg in ("the nurse will complete the visit tomorrow",
                    "the coordinator can fill the form",
                    "yesterday I had to fill the form manually",
                    "he wants to take over the participant"):
            self.assertIsNone(formflow.detect_intent(msg), msg)

    def test_non_novatek_forms_never_fire(self):
        for msg in ("fill out this google form for my survey",
                    "fill the tax form",
                    "complete the excel form",
                    "submit the pdf form"):
            self.assertIsNone(formflow.detect_intent(msg), msg)

    def test_unrelated_messages_pass_through(self):
        for msg in ("open novatek", "play some music", "check my email",
                    "remember that I like coffee", "open chrome on Hossam profile",
                    "", "   "):
            self.assertIsNone(formflow.detect_intent(msg), msg)

    def test_visits_count_clamped(self):
        _, params = formflow.detect_intent("take over the participant, 99 visits")
        self.assertEqual(params["max_visits"], 20)


class TestTryFormflow(unittest.TestCase):
    def setUp(self):
        self._alive = mock.patch.object(formflow, "_daemon_alive", return_value=True)
        self._alive.start()
        self.addCleanup(self._alive.stop)

    def test_daemon_down_gives_guidance_not_launch(self):
        with mock.patch.object(formflow, "_daemon_alive", return_value=False), \
             mock.patch.object(formflow, "execute_browser_tool",
                               side_effect=AssertionError("must not launch a browser")):
            out = formflow.try_formflow(StubCfg(), "fill the form")
        self.assertIn("isn't running", out["answer"])
        self.assertEqual(out["report"]["error"], "browser daemon not running")

    def test_non_command_never_touches_the_daemon(self):
        with mock.patch.object(formflow, "execute_browser_tool",
                               side_effect=AssertionError("daemon must not be called")):
            self.assertIsNone(formflow.try_formflow(StubCfg(), "hello there"))

    def test_form_intent_calls_autofill_form(self):
        report = {"status": "autofill_done", "submitted": True, "submit_verified": True,
                  "answered": [1, 2, 3], "unresolved": [], "rounds": 4,
                  "progress_before": "0/4", "progress_after": "1/4", "widgets": ["x"]}
        with mock.patch.object(formflow, "execute_browser_tool",
                               return_value=dict(report)) as ex:
            out = formflow.try_formflow(StubCfg(), "fill the current form")
        ex.assert_called_once()
        self.assertEqual(ex.call_args[0][1], "browser_autofill_form")
        self.assertEqual(out["intent"], "form")
        self.assertNotIn("widgets", out["report"])  # stripped for transport
        self.assertIn("1/4", out["answer"])
        self.assertIn("3 answers", out["answer"])

    def test_visit_intent_is_single_takeover(self):
        report = {"status": "takeover_done", "visits_completed": 1,
                  "visits": [{"visit": "Screening Visit", "progress": "4/4",
                              "ended": True, "forms": []}]}
        with mock.patch.object(formflow, "execute_browser_tool",
                               return_value=dict(report)) as ex:
            out = formflow.try_formflow(StubCfg(), "complete the visit")
        self.assertEqual(ex.call_args[0][1], "browser_takeover_participant")
        self.assertEqual(ex.call_args[0][2], {"max_visits": 1})
        self.assertIn("Visit completed and ended", out["answer"])

    def test_takeover_intent_passes_visit_count(self):
        report = {"status": "no_next_visit", "visits_completed": 2, "visits": []}
        with mock.patch.object(formflow, "execute_browser_tool",
                               return_value=dict(report)) as ex:
            out = formflow.try_formflow(StubCfg(), "take over the participant, 9 visits")
        self.assertEqual(ex.call_args[0][2], {"max_visits": 9})
        self.assertIn("no further visit remains", out["answer"])

    def test_daemon_error_is_relayed_verbatim(self):
        report = {"error": "Not on the Visit Mode screen — the autopilot is restricted "
                           "to visit mode. Start the visit first."}
        with mock.patch.object(formflow, "execute_browser_tool", return_value=dict(report)):
            out = formflow.try_formflow(StubCfg(), "fill the form")
        self.assertIn("Not on the Visit Mode screen", out["answer"])

    def test_incomplete_form_lists_unresolved(self):
        report = {"status": "autofill_incomplete", "submitted": False,
                  "submit_verified": False, "answered": [1], "rounds": 9,
                  "unresolved": ["7. Weight: still unanswered after 3 attempts"],
                  "progress_before": "0/4", "progress_after": None}
        with mock.patch.object(formflow, "execute_browser_tool", return_value=dict(report)):
            out = formflow.try_formflow(StubCfg(), "fill the form")
        self.assertIn("incomplete", out["answer"].lower())
        self.assertIn("7. Weight", out["answer"])
        self.assertIn("Submit was not reached", out["answer"])


class TestHandleChatRouting(unittest.TestCase):
    """handle_chat must answer form commands without any provider call."""

    def setUp(self):
        self._alive = mock.patch.object(formflow, "_daemon_alive", return_value=True)
        self._alive.start()
        self.addCleanup(self._alive.stop)

    def test_chat_routes_before_model(self):
        from app import controllers
        report = {"status": "autofill_done", "submitted": True, "submit_verified": True,
                  "answered": [], "unresolved": [], "rounds": 1,
                  "progress_before": "0/1", "progress_after": "1/1"}
        with mock.patch.object(formflow, "execute_browser_tool", return_value=dict(report)), \
             mock.patch.object(controllers, "call_model",
                               side_effect=AssertionError("AI provider must not be called")), \
             mock.patch.object(controllers, "regenerate_graph",
                               side_effect=AssertionError("graph must not be rebuilt")):
            resp, status = controllers.handle_chat(
                StubCfg(), "/nonexistent-notes", "/nonexistent-viewer",
                {"message": "fill the current form", "session_id": "t1"})
        self.assertEqual(status, 200)
        self.assertEqual(resp["formflow"], "form")
        self.assertIn("submitted", resp["answer"].lower())

    def test_normal_chat_still_reaches_model(self):
        from app import controllers
        with mock.patch.object(formflow, "execute_browser_tool",
                               side_effect=AssertionError("daemon must not be called")), \
             mock.patch.object(controllers, "call_model", return_value="model answer"), \
             mock.patch.object(controllers, "regenerate_graph",
                               return_value={"nodes": []}):
            resp, status = controllers.handle_chat(
                StubCfg(), "/nonexistent-notes", "/nonexistent-viewer",
                {"message": "tell me about the weather", "session_id": "t2"})
        self.assertEqual(status, 200)
        self.assertEqual(resp["answer"], "model answer")
        self.assertNotIn("formflow", resp)


if __name__ == "__main__":
    unittest.main()
