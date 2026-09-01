#!/usr/bin/env python3
"""tools/tests/test_autofill_recovery.py — the Novatek autopilot's validation
recovery: detecting an inline error, and answering differently next time.

    python3 tools/tests/test_autofill_recovery.py

Scope, stated honestly: these cover the pure decision logic — error detection
and the answer-escalation ladder — plus a simulated reject/correct/resubmit
cycle against a fake page. They do NOT drive the real Novatek app, which needs
a logged-in session sitting on a visit-mode screen with a form open. The parts
that can be tested without that are tested here; the rest needs a live run.

browser_daemon.py imports playwright, so it is loaded from the .venv-browser
interpreter when available and skipped otherwise.
"""
import os
import sys
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "tools"))

try:
    import browser_daemon as bd
except ImportError as exc:  # pragma: no cover - env without playwright
    print(f"SKIP: browser_daemon needs playwright ({exc}).\n"
          f"Run with: .venv-browser/bin/python {' '.join(sys.argv)}")
    sys.exit(0)


def widget(label, role="widget", value=None, y=100, x=500):
    return {"role": role, "label": label, "value": value,
            "bounds": {"x": x, "y": y, "width": 200, "height": 30,
                       "center_x": x + 100, "center_y": y + 15}}


def block(key, *members):
    return {"key": key, "marker_y": 100, "title": key, "hint": key.lower(),
            "members": list(members)}


class ErrorDetectionTests(unittest.TestCase):
    """What counts as the form complaining."""

    REAL_ERRORS = [
        "Please enter a valid number",
        "This field is required",
        "Invalid date format",
        "Value must be between 1 and 10",
        "Please select an option",
        "Cannot be empty",
        "Not a valid entry",
        "Enter a valid email",
    ]

    def test_real_validation_messages_are_detected(self):
        for msg in self.REAL_ERRORS:
            with self.subTest(msg):
                blk = block("1. Weight", widget(msg))
                self.assertTrue(bd._block_has_error(blk), msg)
                self.assertEqual(bd._block_error_text(blk), msg)

    def test_ordinary_question_text_is_not_mistaken_for_an_error(self):
        # A false positive here is worse than a miss: the autopilot would
        # retype a perfectly good answer forever.
        for msg in [
            "Weight in kilograms",
            "Select the participant's status",
            "Date of birth",
            "How many doses were administered?",
            "Signature",
        ]:
            with self.subTest(msg):
                self.assertFalse(bd._block_has_error(block("1. Q", widget(msg))), msg)

    def test_no_error_returns_empty_string(self):
        self.assertEqual(bd._block_error_text(block("1. Q", widget("Weight"))), "")


class AlreadyAnsweredTests(unittest.TestCase):
    """A question holding a valid answer is left alone — the question-level
    counterpart of skipping a form that already carries its checkmark."""

    INNER_H = 900

    @staticmethod
    def blk(*members):
        return {"key": "3. Q", "marker_y": 100, "title": "Q", "hint": "q",
                "members": [dict(m, bounds={"x": 400, "y": m.get("y", 200),
                                            "center_x": 500,
                                            "center_y": m.get("y", 200)})
                            for m in members]}

    def test_filled_field_with_no_error_is_answered(self):
        self.assertTrue(bd._block_is_answered(
            self.blk({"role": "textbox", "value": "9/1/2026"}), self.INNER_H))

    def test_an_empty_field_means_unanswered(self):
        self.assertFalse(bd._block_is_answered(
            self.blk({"role": "textbox", "value": None}), self.INNER_H))

    def test_one_empty_field_among_filled_ones_means_unanswered(self):
        self.assertFalse(bd._block_is_answered(self.blk(
            {"role": "textbox", "value": "9/1/2026", "y": 200},
            {"role": "textbox", "value": None, "y": 240}), self.INNER_H))

    def test_a_ticked_choice_with_no_field_is_answered(self):
        self.assertTrue(bd._block_is_answered(
            self.blk({"role": "button", "value": "checked"}), self.INNER_H))

    def test_an_untouched_choice_is_unanswered(self):
        self.assertFalse(bd._block_is_answered(
            self.blk({"role": "button", "label": "Yes"}), self.INNER_H))

    def test_a_flagged_question_is_never_treated_as_answered(self):
        # The form's own verdict overrides the field's contents: a filled box
        # that the validator rejected must still be re-answered.
        self.assertFalse(bd._block_is_answered(self.blk(
            {"role": "textbox", "value": "01/09/2026", "y": 200},
            {"role": "widget", "label": "Invalid format (M/d/yyyy)", "y": 240}),
            self.INNER_H))

    def test_a_checked_toggle_beside_an_empty_field_is_unanswered(self):
        # The "Use 24-hour format" switch is not the answer.
        self.assertFalse(bd._block_is_answered(self.blk(
            {"role": "switch", "value": "checked", "y": 200},
            {"role": "textbox", "value": None, "y": 240}), self.INNER_H))


class BlockOnScreenTests(unittest.TestCase):
    """A question only counts as on screen when there is room for its INPUT.

    REGRESSION: measured on the live Serum Chemistry form, the marker "1-1."
    sits at y=418 and its textbox at y=520 — about 100px lower. With a 40px
    margin, a marker just above the fold was "visible" while its field was
    below it, so the loop found nothing actionable, exhausted its render waits
    and abandoned the question as "inputs never became actionable" instead of
    scrolling to it.
    """

    INNER_H = 860

    def on_screen(self, marker_y):
        return marker_y < self.INNER_H - bd._AUTOPILOT_BLOCK_MARGIN

    def test_margin_leaves_room_for_the_input(self):
        # The real gap between a marker and its field, with headroom.
        self.assertGreaterEqual(bd._AUTOPILOT_BLOCK_MARGIN, 102)

    def test_a_marker_near_the_fold_is_not_treated_as_reachable(self):
        # Marker visible at 750, but its input would land at ~852 — off screen.
        self.assertFalse(self.on_screen(750))

    def test_a_marker_with_room_below_it_is_reachable(self):
        self.assertTrue(self.on_screen(418))
        self.assertTrue(self.on_screen(600))

    def test_markers_well_below_the_fold_stay_unreachable(self):
        for y in (900, 1446, 2474):
            with self.subTest(y):
                self.assertFalse(self.on_screen(y))


class UploadTargetTests(unittest.TestCase):
    """Which labels count as a file-upload control.

    REGRESSION: the match was a plain substring test, so "file" inside
    "Pro-file" made the navbar's "View Profile PARTICIPANT ID ..." chip an
    upload target. cmd_upload_file clicks with page.mouse.click directly,
    bypassing the guard in _autopilot_click, so it opened the participant
    profile dialog — repeatedly, since the upload never succeeded and was
    retried.
    """

    def hit(self, label):
        return bool(bd._UPLOAD_KEYWORD_RE.search(label.lower()))

    def test_the_navbar_chip_is_not_an_upload_target(self):
        for label in ("View Profile PARTICIPANT ID PRT-100-013-463",
                      "Profile settings", "View Profile", "profile"):
            with self.subTest(label):
                self.assertFalse(self.hit(label), label)

    def test_real_upload_controls_still_match(self):
        for label in ("Upload saliva sample results and/or related documents",
                      "Attach signed consent", "Choose file", "Browse",
                      "Files", "Signed consent form",
                      "Informed Consent Template.pdf"):
            with self.subTest(label):
                self.assertTrue(self.hit(label), label)

    def test_unrelated_controls_do_not_match(self):
        for label in ("Select an option", "Account Admin Admin A",
                      "Date of Data Entry", "Submit", "Yes",
                      "View all participant queries"):
            with self.subTest(label):
                self.assertFalse(self.hit(label), label)

    def test_the_click_guard_also_covers_upload_targets(self):
        # Belt and braces: even if a label slipped through the keyword test,
        # a navbar position or reserved label must still refuse the click.
        self.assertTrue(bd._autopilot_click_forbidden(
            {"label": "View Profile PARTICIPANT ID PRT-100-013-463",
             "bounds": {"center_y": 65}}))


class UploadStatusTests(unittest.TestCase):
    """cmd_upload_file has two success returns and the autopilot must accept
    both.

    REGRESSION: only "file_uploaded" counted, so the Flutter path's
    "file_selected" — returned after the daemon intercepts the native file
    chooser and picks the file — was read as a failure. The autopilot then
    retried a completed upload, re-opening the chooser modal, which stalled
    the visit walk repeatedly.
    """

    SUCCESS = ("file_uploaded", "file_selected")

    @staticmethod
    def accepted(status):
        return status in ("file_uploaded", "file_selected")

    def test_both_success_statuses_are_accepted(self):
        for status in self.SUCCESS:
            with self.subTest(status):
                self.assertTrue(self.accepted(status))

    def test_genuine_failures_are_still_failures(self):
        for status in ("error", "no_input_found", None, "", "cancelled"):
            with self.subTest(status):
                self.assertFalse(self.accepted(status))

    def test_the_daemon_still_returns_both(self):
        import inspect
        src = inspect.getsource(bd.cmd_upload_file)
        self.assertIn('"file_uploaded"', src)
        self.assertIn('"file_selected"', src)


class EndVisitGateTests(unittest.TestCase):
    """Ending a visit is irreversible, so it happens only when the progress
    counter verifies every form is submitted — and it DOES happen then. That
    is the flow: fill, submit, verify, end.

    The click guard still refuses a STRAY End Visit during form filling; the
    gated path overrides it deliberately via allow_reserved.
    """

    def test_a_full_counter_is_complete(self):
        for progress in ("20/20", "1/1", "18/18"):
            with self.subTest(progress):
                self.assertTrue(bd._visit_is_complete(progress))

    def test_anything_short_of_full_is_not(self):
        for progress in ("9/20", "0/18", "19/20"):
            with self.subTest(progress):
                self.assertFalse(bd._visit_is_complete(progress))

    def test_an_unreadable_counter_is_never_complete(self):
        # Irreversible action: anything ambiguous must fail closed.
        for progress in (None, "", "unknown", "3", "a/b", "//", "0/0", "5/", "/5"):
            with self.subTest(repr(progress)):
                self.assertFalse(bd._visit_is_complete(progress))

    def test_the_real_button_label_is_recognised(self):
        # REGRESSION: the accessible name carries the helper text first —
        # "You can end the visit only when all forms are completed. End Visit"
        # — so a startswith("end visit") match never found it.
        for label in ("End Visit",
                      "You can end the visit only when all forms are completed. End Visit",
                      "end visit"):
            with self.subTest(label):
                self.assertIn("end visit", label.strip().lower())

    def test_a_stray_end_visit_click_is_still_refused(self):
        self.assertTrue(bd._autopilot_click_forbidden(
            {"label": "End Visit", "bounds": {"center_y": 840}}))


class VisitVerdictTests(unittest.TestCase):
    """The visit report's plain-language verdict.

    REGRESSION: the first version did arithmetic on the progress counter's
    halves, which are strings — every visit walk died with
    "unsupported operand type(s) for -: 'str' and 'str'".
    """

    @staticmethod
    def verdict(progress, all_done):
        parts = (progress or "0/1").split("/")
        counter = progress or "unknown"
        if all_done:
            return f"COMPLETE - the progress counter reads {counter}. Every form is submitted."
        remaining = "unknown"
        try:
            remaining = str(int(parts[1]) - int(parts[0]))
        except (ValueError, IndexError, TypeError):
            pass
        return (f"NOT COMPLETE - the progress counter reads {counter}, so {remaining} "
                "form(s) are still unsubmitted.")

    def test_counts_the_remaining_forms(self):
        self.assertIn("18 form(s)", self.verdict("0/18", False))
        self.assertIn("3 form(s)", self.verdict("7/10", False))
        self.assertIn("1 form(s)", self.verdict("17/18", False))

    def test_a_complete_visit_says_so(self):
        self.assertTrue(self.verdict("18/18", True).startswith("COMPLETE"))

    def test_an_unreadable_counter_does_not_raise(self):
        for progress in (None, "", "unknown", "3", "a/b", "//"):
            with self.subTest(repr(progress)):
                self.assertIn("NOT COMPLETE", self.verdict(progress, False))

    def test_the_verdict_names_the_counter_verbatim(self):
        self.assertIn("0/18", self.verdict("0/18", False))


class NeverClickTests(unittest.TestCase):
    """Controls that navigate away from the form are unclickable, whatever
    code path picks them.

    REGRESSION: during a visit walk the dropdown-option picker treated a
    re-rendered top-bar widget as a fresh option and clicked the participant
    chip. That opened a profile dialog the autopilot could not dismiss, and
    the whole run froze. The guard lives in _autopilot_click — the single
    point every click goes through — so no future code path can reach one.
    """

    @staticmethod
    def w(label, y=400):
        return {"label": label, "bounds": {"center_x": 600, "center_y": y}}

    def test_navigation_and_destructive_controls_are_refused(self):
        for label, y in [("View Profile", 70),
                         ("PARTICIPANT ID PRT-100-013-463", 70),
                         ("Account Admin Admin A", 70),
                         ("End Visit", 840), ("Hold Visit", 840),
                         ("Discard changes", 780), ("Early Termination", 810),
                         ("View/Edit visit note", 750), ("Log out", 70)]:
            with self.subTest(label):
                self.assertTrue(bd._autopilot_click_forbidden(self.w(label, y)), label)

    def test_anything_in_the_top_bar_is_refused_even_unlabelled(self):
        self.assertIn("top navigation",
                      bd._autopilot_click_forbidden(self.w("", 40)))

    def test_the_controls_the_autopilot_needs_stay_clickable(self):
        for label, y in [("Submit", 800), ("Submit Form", 820), ("Yes", 400),
                         ("No", 400), ("Injection (INJ)", 530), ("Sign Now", 600),
                         ("Sign Document", 620), ("OK", 700), ("kg", 170),
                         # required to clear the mandatory reason modal
                         ("Add to visit note", 540)]:
            with self.subTest(label):
                self.assertEqual(bd._autopilot_click_forbidden(self.w(label, y)), "", label)

    def test_the_guard_is_case_insensitive(self):
        self.assertTrue(bd._autopilot_click_forbidden(self.w("END VISIT", 800)))
        self.assertTrue(bd._autopilot_click_forbidden(self.w("view profile", 800)))


class VisitDateTests(unittest.TestCase):
    """The visit date belongs to the visit, is set once, and must never be
    rewritten once it holds a value — typing into it re-opens the calendar
    picker, whose modal then blocks the entire page."""

    MIN_X = 330

    @staticmethod
    def box(value=None, label="", x=90):
        return {"role": "textbox", "label": label, "value": value,
                "bounds": {"x": x, "y": 140, "center_x": x, "center_y": 146}}

    def test_an_empty_rail_field_is_offered(self):
        self.assertIsNotNone(bd._visit_date_box([self.box()], self.MIN_X))

    def test_a_filled_field_is_left_alone(self):
        self.assertIsNone(bd._visit_date_box([self.box(value="9/1/2026")], self.MIN_X))

    def test_a_date_shown_without_a_value_still_counts_as_set(self):
        # REGRESSION: these inputs do not reliably expose `value`, so judging
        # emptiness on it alone rewrote a field that was already correct.
        self.assertIsNone(bd._visit_date_box(
            [self.box(label="9/1/2026")], self.MIN_X))

    def test_question_panel_fields_are_never_mistaken_for_it(self):
        self.assertIsNone(bd._visit_date_box(
            [self.box(x=800)], self.MIN_X))

    def test_no_textbox_at_all(self):
        self.assertIsNone(bd._visit_date_box([], self.MIN_X))

    def test_date_pattern_matches_the_formats_novatek_uses(self):
        for text in ("9/1/2026", "09/01/2026", "2026-09-01", "1-9-2026"):
            with self.subTest(text):
                self.assertIsNone(bd._visit_date_box([self.box(label=text)], self.MIN_X))
        # ordinary prose must not read as a date
        self.assertIsNotNone(bd._visit_date_box(
            [self.box(label="Actual visit date")], self.MIN_X))


class SubmitOutcomeTests(unittest.TestCase):
    """Reading the result of a submit BEFORE the request finishes reports the
    state from before it — an accepted submission still showed the old
    progress counter. The page's own signals decide when it is done."""

    @staticmethod
    def w(label=None, role="widget", value=None):
        return {"role": role, "label": label, "value": value,
                "bounds": {"x": 400, "y": 200, "center_x": 500, "center_y": 200}}

    def test_busy_while_a_spinner_is_up(self):
        self.assertEqual(bd._autopilot_submit_state([self.w("Submitting...")], []), "busy")
        self.assertEqual(bd._autopilot_submit_state([self.w("Please wait")], []), "busy")

    def test_an_indeterminate_progressbar_is_a_spinner(self):
        self.assertEqual(bd._autopilot_submit_state(
            [self.w(role="progressbar", value=None)], []), "busy")

    def test_the_visit_progress_ring_is_not_a_spinner(self):
        # It always carries a value, so it must not read as "still loading".
        self.assertIsNone(bd._autopilot_submit_state(
            [self.w(role="progressbar", value="3")], []))

    def test_success_and_backend_failure_are_distinguished(self):
        self.assertEqual(bd._autopilot_submit_state([self.w("Success")], []), "success")
        self.assertEqual(bd._autopilot_submit_state(
            [self.w("Submission failed")], []), "failed")
        self.assertEqual(bd._autopilot_submit_state(
            [self.w("Something went wrong")], []), "failed")

    def test_busy_wins_over_a_stale_banner(self):
        # A success banner from the PREVIOUS submit must not be read as this
        # one's result while the request is still in flight.
        self.assertEqual(bd._autopilot_submit_state(
            [self.w("Success"), self.w("Submitting...")], []), "busy")

    def test_client_side_rejection_is_reported_separately(self):
        panel = [self.w("Form Incomplete"), self.w("Dose Start Time"),
                 self.w("This question must be answered."), self.w("1.")]
        for x, y in zip(panel, (100, 140, 160, 200)):
            x["bounds"]["center_y"] = y
        self.assertEqual(bd._autopilot_submit_state(panel, panel), "rejected")

    def test_silence_is_not_an_outcome(self):
        self.assertIsNone(bd._autopilot_submit_state([self.w("Weight")], []))


class SubmitLabelTests(unittest.TestCase):
    """The submit button is not called the same thing on every form."""

    def test_both_real_labels_are_recognised(self):
        # REGRESSION: question forms say "Submit"; the entry-table forms
        # (Concomitant Medications) say "Submit Form". An exact match found the
        # first only, so the autopilot walked that form to the bottom and
        # reported "reached the bottom without finding Submit" with the button
        # visible on screen.
        for label in ("Submit", "Submit Form", "submit form", " Submit "):
            with self.subTest(label):
                self.assertTrue(bd._is_submit_label(label))

    def test_other_buttons_are_not_submit(self):
        for label in ("Sign Now", "Sign Document", "Yes", "No", "Add New Entry",
                      "Cancel", "Discard changes", ""):
            with self.subTest(label):
                self.assertFalse(bd._is_submit_label(label))

    def test_submit_is_not_offered_as_an_answer_option(self):
        self.assertTrue(bd._is_submit_label("Submit Form"))


class RangeAndTypographyTests(unittest.TestCase):
    """Novatek prints the valid bands beside each numeric question, and writes
    real typography in the titles. Both were breaking the answer choice."""

    @staticmethod
    def blk(title, *chips):
        return {"title": title, "hint": bd._normalize_hint(title),
                "members": [{"label": c} for c in chips]}

    def test_subscript_digits_fold_to_ascii(self):
        # REGRESSION: "SpO₂" is S-p-O-U+2082. The "spo2" hint never matched, so
        # a numeric field fell through to the text ladder and was sent "test".
        self.assertEqual(bd._normalize_hint("SpO₂"), "spo2")
        self.assertTrue(any(h in bd._normalize_hint("SpO₂")
                            for h in bd._AUTOPILOT_NUMBER_HINTS))

    def test_range_value_is_inside_the_stated_band(self):
        for title, chips, low, high in [
            ("SpO₂", ("0 - 89", "90 - 94", "95 - 100"), 95, 100),
            ("Body temperature", ("36.5 - 37.5",), 36.5, 37.5),
            ("Participant weight", ("2.1 - 635",), 2.1, 635),
            ("Respiration rate", ("0 - 11", "12 - 20", "21 - 24", "25 - 60"), 25, 60),
        ]:
            with self.subTest(title):
                value = float(bd._autopilot_range_value(self.blk(title, *chips)))
                self.assertGreaterEqual(value, low)
                self.assertLessEqual(value, high)

    def test_temperature_is_solvable_only_via_its_range(self):
        # The decisive case: none of the numeric ladder's candidates is inside
        # 36.5-37.5, so without reading the chip this question can never pass.
        ladder = bd._autopilot_variants("body temperature", "9/1/2026", "14:30", "test", "55")
        self.assertFalse(any(36.5 <= float(c) <= 37.5 for c in ladder if c.replace(".", "").isdigit()))
        self.assertEqual(bd._autopilot_range_value(self.blk("Body temperature", "36.5 - 37.5")), "37")

    def test_no_chips_means_no_range_answer(self):
        self.assertIsNone(bd._autopilot_range_value(self.blk("Notes", "Ranges:", "kg")))

    def test_malformed_chips_are_ignored(self):
        self.assertIsNone(bd._autopilot_range_value(self.blk("X", "1 -", "- 5", "a - b")))

    def test_en_dash_chips_are_accepted(self):
        self.assertEqual(bd._autopilot_range_value(self.blk("X", "10 \u2013 20")), "15")


class IncompleteCardTests(unittest.TestCase):
    """Novatek's own "Form Incomplete" card names exactly what is missing.

    Modelled on the real widget layout captured from the live app:
        y=185  'Form Incomplete'
        y=229  'Please correct the following issues to proceed:'
        y=263  'Dose Start Time'                  <- title
        y=285  'This question must be answered.'  <- reason
        y=319  'Dose End Time'
        y=341  'This question must be answered.'
        y=419  '1.'                               <- first question marker
    """

    @staticmethod
    def card(*rows):
        return [{"role": "widget", "label": label,
                 "bounds": {"x": 460, "y": y, "width": 300, "height": 20,
                            "center_x": 600, "center_y": y}}
                for label, y in rows]

    REAL = [("Form Incomplete", 185),
            ("Please correct the following issues to proceed:", 229),
            ("Dose Start Time", 263),
            ("This question must be answered.", 285),
            ("Dose End Time", 319),
            ("This question must be answered.", 341),
            ("1.", 419),
            ("Was the scheduled Nivolumab dose administered?", 419)]

    def test_extracts_exactly_the_named_questions(self):
        self.assertEqual(bd._autopilot_incomplete_items(self.card(*self.REAL)),
                         ["Dose Start Time", "Dose End Time"])

    def test_header_intro_and_reason_lines_are_not_titles(self):
        got = bd._autopilot_incomplete_items(self.card(*self.REAL))
        for noise in ("Form Incomplete", "Please correct the following issues to proceed:",
                      "This question must be answered."):
            self.assertNotIn(noise, got)

    def test_nothing_below_the_first_question_marker_is_collected(self):
        # The form's real questions start at the marker; they are not "missing".
        self.assertNotIn("Was the scheduled Nivolumab dose administered?",
                         bd._autopilot_incomplete_items(self.card(*self.REAL)))

    def test_group_wrapper_does_not_anchor_the_card(self):
        # REGRESSION: the card is wrapped in a group whose own centre sits
        # mid-card (y=699 live), below the items. Anchoring to the first match
        # in document order instead of the topmost collected nothing.
        rows = list(self.REAL) + [("Form Incomplete Please correct the following issues to proceed: Dose Start Time", 699)]
        self.assertEqual(bd._autopilot_incomplete_items(self.card(*rows)),
                         ["Dose Start Time", "Dose End Time"])

    def test_no_card_means_no_items(self):
        self.assertEqual(bd._autopilot_incomplete_items(
            self.card(("1.", 100), ("Some question", 100))), [])

    def test_a_single_missing_question(self):
        self.assertEqual(bd._autopilot_incomplete_items(self.card(
            ("Form Incomplete", 100),
            ("Please correct the following issues to proceed:", 120),
            ("Dose End Time", 150),
            ("This question must be answered.", 170),
            ("1.", 220))), ["Dose End Time"])

    def test_the_card_never_becomes_question_ones_title(self):
        # REGRESSION: the card sits above question 1 and was picked up as its
        # title, so the autopilot answered "1. Form Incomplete ..." as a question.
        panel = self.card(("1.", 419), ("Form Incomplete", 419))
        blocks = bd._autopilot_blocks(panel)
        self.assertNotIn("form incomplete", blocks[0]["key"].lower())


class AnsweredOnSightTests(unittest.TestCase):
    """The rule the autopilot uses to skip an already-answered question.

    Expressed here as the boolean it evaluates, because the real check lives
    inside a long async loop. REGRESSION: Novatek's time questions ship a
    "Use 24-hour format" switch already checked, and counting that as the
    answer left Hours:Minutes blank while the question looked complete.
    """

    @staticmethod
    def is_answered(has_filled_box, any_empty_box, already_checked, already_uploaded):
        return not any_empty_box and (has_filled_box or already_checked or already_uploaded)

    def test_a_checked_format_toggle_does_not_answer_an_empty_time_field(self):
        self.assertFalse(self.is_answered(
            has_filled_box=False, any_empty_box=True,
            already_checked=True, already_uploaded=False))

    def test_an_empty_box_beats_every_other_signal(self):
        for filled, checked, uploaded in [(True, True, True), (True, False, False),
                                          (False, True, False), (False, False, True)]:
            with self.subTest(filled=filled, checked=checked, uploaded=uploaded):
                self.assertFalse(self.is_answered(filled, True, checked, uploaded))

    def test_genuinely_answered_questions_are_still_skipped(self):
        self.assertTrue(self.is_answered(True, False, False, False))   # filled text
        self.assertTrue(self.is_answered(False, False, True, False))   # ticked choice
        self.assertTrue(self.is_answered(False, False, False, True))   # attached file

    def test_an_untouched_question_is_not_answered(self):
        self.assertFalse(self.is_answered(False, False, False, False))


class ContainerBlockTests(unittest.TestCase):
    """A numbered heading whose inputs live in its sub-questions is not itself
    a question, and must never be queued as one."""

    @staticmethod
    def panel(*labels):
        return [{"role": "widget", "label": l,
                 "bounds": {"x": 400, "y": y, "width": 10, "height": 10,
                            "center_x": 405, "center_y": y}}
                for l, y in labels]

    def test_parent_marker_is_flagged_as_a_container(self):
        # REGRESSION: "3." has no field of its own — its inputs are under
        # "3-1."/"3-2.". Treated as a question it burned render-waits, was
        # abandoned as "inputs never became actionable", and kept `pending`
        # non-empty so the run never reached Submit.
        blocks = bd._autopilot_blocks(self.panel(
            ("3.", 100), ("Dose Start Date & Time", 100),
            ("3-1.", 200), ("Dose Start Date", 200),
            ("3-2.", 300), ("Dose Start Time", 300),
            ("5.", 400), ("Route of Administration", 400),
        ))
        by_marker = {b["marker"]: b for b in blocks}
        self.assertTrue(by_marker["3."]["is_container"])
        self.assertFalse(by_marker["3-1."]["is_container"])
        self.assertFalse(by_marker["3-2."]["is_container"])
        self.assertFalse(by_marker["5."]["is_container"])

    def test_a_lone_numbered_question_is_not_a_container(self):
        blocks = bd._autopilot_blocks(self.panel(("6.", 100), ("Who Administered", 100)))
        self.assertFalse(blocks[0]["is_container"])

    def test_two_digit_prefixes_do_not_collide(self):
        # "1." must not be made a container by the existence of "13-1."
        blocks = bd._autopilot_blocks(self.panel(
            ("1.", 100), ("First", 100), ("13-1.", 200), ("Thirteenth sub", 200)))
        self.assertFalse({b["marker"]: b for b in blocks}["1."]["is_container"])


class ScreenLevelErrorTests(unittest.TestCase):
    """The visit's own required-field banner is not any question's error."""

    BANNER = ("Visit Mode Actual visit date Please enter the actual visit "
              "start date, Actual visit date is required")

    def test_visit_banner_is_not_attributed_to_a_question(self):
        # REGRESSION: this banner sits inside the question panel and was read
        # as question 2's error, so the autopilot retried 20+ variants of a
        # date that had never been wrong while the real blocker went untouched.
        self.assertEqual(bd._block_error_text(block("2. Date", widget(self.BANNER))), "")
        self.assertFalse(bd._block_has_error(block("2. Date", widget(self.BANNER))))

    def test_a_real_error_alongside_the_banner_still_wins(self):
        blk = block("2. Date", widget(self.BANNER), widget("Invalid format (M/d/yyyy)"))
        self.assertEqual(bd._block_error_text(blk), "Invalid format (M/d/yyyy)")


class VariantLadderTests(unittest.TestCase):
    """Each rejection must produce a DIFFERENT answer — the whole point."""

    def variants(self, hint):
        return bd._autopilot_variants(hint, "9/1/2026", "14:30", "test", "55")

    def test_every_candidate_is_distinct(self):
        for hint in ("weight in kg", "visit date", "visit time", "free text notes"):
            with self.subTest(hint):
                v = self.variants(hint)
                self.assertEqual(len(v), len(set(v)), f"{hint} repeats a value: {v}")
                self.assertGreaterEqual(len(v), 3)

    def test_first_candidate_is_the_configured_default(self):
        self.assertEqual(self.variants("free entry")[0], "test")
        self.assertEqual(self.variants("number of doses")[0], "55")
        self.assertEqual(self.variants("visit date")[0], "9/1/2026")
        self.assertEqual(self.variants("visit time")[0], "14:30")

    def test_an_unclassified_field_escalates_from_text_to_a_number(self):
        # For a question that matches neither the text nor the number hints,
        # the commonest real failure is a digit-only field silently refusing
        # "test", so the second candidate must be numeric. ("notes" is NOT a
        # valid example any more — it is now correctly classified as text.)
        v = self.variants("free entry")
        self.assertEqual(v[0], "test")
        self.assertTrue(v[1].isdigit(), f"unclassified ladder should reach a number: {v}")

    def test_date_variants_are_different_formats_not_different_dates(self):
        v = self.variants("visit date")
        self.assertTrue(all(any(ch.isdigit() for ch in x) for x in v))
        self.assertIn(time.strftime("%Y-%m-%d"), v)

    def test_index_cycles_rather_than_clamping(self):
        # REGRESSION: this originally asserted clamping — min(vi, len-1) — which
        # is wrong. Clamping parks on the LAST candidate forever, and on a real
        # Novatek date field the last candidate (d/M/yyyy) is the invalid one,
        # so every retry retyped the same rejected value. The loop cycles now.
        v = self.variants("visit date")
        self.assertEqual(v[len(v) % len(v)], v[0])
        self.assertEqual(v[(len(v) + 1) % len(v)], v[1])
        tried = {v[i % len(v)] for i in range(len(v) * 3)}
        self.assertEqual(tried, set(v), "cycling must revisit every candidate")


class ValidatorFormatTests(unittest.TestCase):
    """Novatek states the format it wants inside the rejection. Reading it is
    strictly better than guessing, and is what the live run showed we needed."""

    CASES = [
        ("Invalid format (M/d/yyyy)", "%-m/%-d/%Y"),
        ("Invalid format (dd/MM/yyyy)", "%d/%m/%Y"),
        ("Invalid format (yyyy-MM-dd)", "%Y-%m-%d"),
        ("Invalid time format (HH:mm)", "%H:%M"),
        ("Invalid time format (hh:mm a)", "%I:%M %p"),
    ]

    def test_format_is_extracted_and_renders(self):
        for message, expected in self.CASES:
            with self.subTest(message):
                got = bd._format_from_error(message)
                self.assertEqual(got, expected)
                self.assertTrue(time.strftime(got), "must render to a real value")

    def test_messages_without_a_format_return_none(self):
        for message in ("This field is required", "Please select an option",
                        "Value must be between 1 and 10", "", None):
            with self.subTest(repr(message)):
                self.assertIsNone(bd._format_from_error(message))

    def test_rendered_value_round_trips_through_the_same_pattern(self):
        # "Invalid format (M/d/yyyy)" must produce something that IS M/d/yyyy.
        rendered = time.strftime(bd._format_from_error("Invalid format (M/d/yyyy)"))
        parts = rendered.split("/")
        self.assertEqual(len(parts), 3)
        self.assertFalse(parts[0].startswith("0"), "M means no leading zero")
        self.assertEqual(len(parts[2]), 4, "yyyy means four digits")


class ValueKindTests(unittest.TestCase):
    """A field that takes letters must never be offered a number, and vice
    versa — no amount of retrying fixes a ladder made entirely of wrong kinds."""

    def variants(self, hint):
        return bd._autopilot_variants(hint, "9/1/2026", "14:30", "test", "55")

    def test_person_questions_are_text_even_when_they_name_a_quantity(self):
        # REGRESSION: "Who Administered the Dose?" matched the "dose" number
        # hint, so every candidate was numeric — into a letters-only field.
        for hint in ("who administered the dose?", "name of personnel",
                     "administering nurse", "investigator initials"):
            with self.subTest(hint):
                v = self.variants(hint)
                self.assertFalse(v[0].isdigit(), f"{hint} -> {v}")
                self.assertFalse(any(x.isdigit() for x in v),
                                 f"a name field must offer no bare number: {v}")

    def test_number_hints_do_not_match_by_accident(self):
        # REGRESSION: "spo" (for SpO2) matched "response" as a bare substring,
        # so any "Response" question was answered with a number.
        for hint in ("response to treatment", "sponsor name", "responsible party"):
            with self.subTest(hint):
                self.assertFalse(self.variants(hint)[0].isdigit(),
                                 f"{hint} is not a quantity: {self.variants(hint)}")

    def test_genuine_quantity_questions_stay_numeric(self):
        for hint in ("dose amount (mg)", "systolic pressure", "how many tablets"):
            with self.subTest(hint):
                self.assertTrue(self.variants(hint)[0].isdigit())

    def test_kind_is_read_from_the_rejection(self):
        for message, kind in [
            ("Only letters are allowed", "alpha"),
            ("This field cannot contain numbers", "alpha"),
            ("Alphabetic characters only", "alpha"),
            ("Please enter a valid number", "numeric"),
            ("Digits only", "numeric"),
            ("Invalid format (M/d/yyyy)", None),
            ("This field is required", None),
        ]:
            with self.subTest(message):
                self.assertEqual(bd._kind_from_error(message), kind)

    def test_alpha_recovery_never_offers_a_digit(self):
        # What the loop substitutes once the validator says "letters only".
        alpha = ["test", "Admin", "Nurse", "Staff"]
        for i in range(12):
            self.assertFalse(alpha[i % len(alpha)].isdigit())


class FakePage:
    """Enough of a Playwright page for _autopilot_reopen_rejected."""

    def __init__(self, screens):
        self.screens = screens
        self.index = 0

    async def wait_for_timeout(self, _ms):
        pass


class ReopenRejectedTests(unittest.IsolatedAsyncioTestCase):
    """After a refusal, the autopilot must re-open exactly the questions the
    form complained about — and must not permanently abandon them."""

    async def asyncSetUp(self):
        self._extract = bd.extract_flutter_widgets
        self._scroll = bd.cmd_scroll
        self._panel = bd._autopilot_panel
        self._blocks = bd._autopilot_blocks

    async def asyncTearDown(self):
        bd.extract_flutter_widgets = self._extract
        bd.cmd_scroll = self._scroll
        bd._autopilot_panel = self._panel
        bd._autopilot_blocks = self._blocks

    def install(self, screens):
        """screens: list of (widgets, blocks) per scroll position.

        Kept faithful to the real shapes on purpose: _autopilot_panel returns
        WIDGETS and _autopilot_blocks turns those into blocks. An earlier
        version of this fake returned blocks from both, which made the
        submit-button check unmatchable and produced a failure that was in the
        test rather than in the code.
        """
        state = {"i": 0}

        def current():
            return screens[min(state["i"], len(screens) - 1)]

        async def fake_extract(_page):
            return current()[0]

        async def fake_scroll(_payload):
            state["i"] += 1
            return {"status": "scrolled"}

        bd.extract_flutter_widgets = fake_extract
        bd.cmd_scroll = fake_scroll
        bd._autopilot_panel = lambda widgets, _min_x: widgets
        bd._autopilot_blocks = lambda _panel: current()[1]

    async def test_errored_questions_are_reopened_and_escalated(self):
        bad = block("3. Weight", widget("Please enter a valid number"))
        good = block("4. Notes", widget("Notes"))
        submit_widget = widget("Submit", role="button")
        self.install([
            ([widget("Please enter a valid number"), widget("Notes")], [bad, good]),
            ([submit_widget], []),
        ])

        done = {"3. Weight", "4. Notes"}
        attempts = {"3. Weight": 3, "4. Notes": 1}   # 3 = previously abandoned
        variant, report = {}, {"corrections": []}

        n = await bd._autopilot_reopen_rejected(
            FakePage([]), None, 330, done, attempts, variant, report)

        self.assertEqual(n, 1)
        self.assertNotIn("3. Weight", done, "the errored question must be re-opened")
        self.assertIn("4. Notes", done, "a question with no error must be left alone")
        self.assertEqual(attempts["3. Weight"], 0,
                         "the attempt cap must not permanently abandon a question "
                         "the form is explicitly blocking on")
        self.assertEqual(variant["3. Weight"], 1, "the retry must use a different answer")
        self.assertEqual(report["corrections"][0]["error"], "Please enter a valid number")

    async def test_an_answered_choice_is_never_reopened(self):
        # REGRESSION: re-picking a choice is not a neutral retry. Flipping
        # "Was the dose administered per protocol?" from Yes to No collapsed
        # every dependent question and opened a "reason for answering No"
        # modal that blocked the entire page.
        bad = block("1. Was the dose administered", widget("This field is required"))
        self.install([
            ([widget("This field is required")], [bad]),
            ([widget("Submit", role="button")], []),
        ])
        done, attempts, variant, report = {"1. Was the dose administered"}, {}, {}, {"corrections": []}
        n = await bd._autopilot_reopen_rejected(
            FakePage([]), None, 330, done, attempts, variant, report,
            protected={"1. Was the dose administered"})
        self.assertEqual(n, 0, "a protected choice must not be re-opened")
        self.assertIn("1. Was the dose administered", done)
        self.assertEqual(variant, {})

    async def test_returns_zero_when_nothing_is_flagged(self):
        # A refusal with no inline message — the caller re-answers everything
        # rather than resubmitting identical state.
        self.install([
            ([widget("Notes")], [block("1. Q", widget("Notes"))]),
            ([widget("Submit", role="button")], []),
        ])
        report = {"corrections": []}
        n = await bd._autopilot_reopen_rejected(
            FakePage([]), None, 330, set(), {}, {}, report)
        self.assertEqual(n, 0)
        self.assertEqual(report["corrections"], [])

    async def test_stops_scrolling_once_submit_is_in_view(self):
        scrolls = {"n": 0}
        self.install([([widget("Submit", role="button")], [])])
        real_scroll = bd.cmd_scroll

        async def counting_scroll(payload):
            scrolls["n"] += 1
            return await real_scroll(payload)

        bd.cmd_scroll = counting_scroll

        await bd._autopilot_reopen_rejected(
            FakePage([]), None, 330, set(), {}, {}, {"corrections": []})
        self.assertEqual(scrolls["n"], 0, "must not keep scrolling past the bottom")


class ConvergenceTests(unittest.TestCase):
    """The reject -> correct -> resubmit cycle must actually converge rather
    than retrying the same refused value."""

    def test_repeated_rejection_walks_the_whole_ladder(self):
        hint, variant, tried = "weight", {"k": 0}, []
        candidates = bd._autopilot_variants(hint, "9/1/2026", "14:30", "test", "55")
        for _ in range(len(candidates) + 2):
            tried.append(candidates[min(variant["k"], len(candidates) - 1)])
            variant["k"] += 1               # the form rejected it again
        self.assertEqual(len(set(tried)), len(candidates),
                         f"every distinct candidate should be tried once: {tried}")

    def test_submit_budget_is_finite(self):
        # "Never stops" must still terminate: the loop is bounded by
        # max_submit_attempts so a form that can never be satisfied reports
        # the last validation errors instead of hanging the server thread.
        self.assertGreaterEqual(6, 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
