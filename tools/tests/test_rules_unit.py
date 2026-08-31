#!/usr/bin/env python
"""Unit tests for the deterministic rule engine inside tools/browser_daemon.py.

Run with the daemon's own interpreter (playwright must be importable):
    .venv-browser/bin/python tools/tests/test_rules_unit.py -v

These cover the pure logic the autopilot is built from — question-block
segmentation, screen guards, the progress counter, validation-error
detection, widget signatures, and the stdlib PNG decoder used for the
sidebar checkmark scan — with zero browser involvement.
"""
import os
import struct
import sys
import unittest
import zlib

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import browser_daemon as bd  # noqa: E402


def w(role="widget", label=None, value=None, x=500, y=200, width=80, height=30):
    return {
        "role": role, "label": label, "value": value,
        "bounds": {"x": x - width // 2, "y": y - height // 2,
                   "width": width, "height": height,
                   "center_x": x, "center_y": y},
    }


class TestMarkerRegex(unittest.TestCase):
    def test_accepts_question_markers(self):
        for m in ("1.", "12.", "4-1.", "10-3."):
            self.assertTrue(bd._AUTOPILOT_MARKER_RE.match(m), m)

    def test_rejects_non_markers(self):
        for m in ("1", "a.", "1.2", ".", "-1.", "1-.", "1a.", "0/4"):
            self.assertFalse(bd._AUTOPILOT_MARKER_RE.match(m), m)


class TestScreenGuards(unittest.TestCase):
    def test_visit_ok_requires_exact_visit_mode_label(self):
        self.assertTrue(bd._autopilot_visit_ok([w(label="Visit Mode")]))
        self.assertFalse(bd._autopilot_visit_ok([w(label="Visit Mode Extra")]))
        self.assertFalse(bd._autopilot_visit_ok([w(label="Dashboard")]))
        self.assertFalse(bd._autopilot_visit_ok([]))

    def test_profile_ok(self):
        self.assertTrue(bd._autopilot_profile_ok([w(label="Visit Progress")]))
        self.assertTrue(bd._autopilot_profile_ok([w(label="Participant profile")]))
        self.assertFalse(bd._autopilot_profile_ok([w(label="Visit Mode")]))


class TestProgress(unittest.TestCase):
    def test_finds_counter(self):
        widgets = [w(label="Visit Mode"), w(label="3 / 9"), w(label="Submit")]
        self.assertEqual(bd._autopilot_progress(widgets), "3/9")

    def test_none_when_absent(self):
        self.assertIsNone(bd._autopilot_progress([w(label="Visit Mode")]))

    def test_ignores_lookalikes(self):
        self.assertIsNone(bd._autopilot_progress([w(label="a/b"), w(label="1/2/3")]))


class TestPanel(unittest.TestCase):
    def test_filters_sidebar_and_topbar(self):
        widgets = [
            w(label="sidebar btn", x=150, y=300),   # left of min_x
            w(label="topbar item", x=600, y=50),    # above y=100
            w(label="q widget", x=600, y=300),
            w(label="earlier", x=400, y=200),
        ]
        panel = bd._autopilot_panel(widgets, 330)
        self.assertEqual([p["label"] for p in panel], ["earlier", "q widget"])


class TestBlocks(unittest.TestCase):
    def _panel(self):
        return [
            w(label="1.", x=360, y=200),
            w(label="First question title", x=520, y=202),
            w(role="textbox", label="First question title", x=500, y=260),
            w(label="some hint text", x=500, y=240),
            w(label="2.", x=360, y=420),
            w(label="Second question", x=500, y=421),
            w(role="button", label="Option Alpha", value="unchecked", x=450, y=480),
            w(role="button", label="Option Beta", value="unchecked", x=560, y=480),
        ]

    def test_two_blocks_with_titles_and_members(self):
        blocks = bd._autopilot_blocks(self._panel())
        self.assertEqual(len(blocks), 2)
        self.assertEqual(blocks[0]["key"], "1. First question title")
        self.assertEqual(blocks[1]["key"], "2. Second question")
        # Block 1 owns everything above marker 2's boundary.
        labels0 = [m.get("label") for m in blocks[0]["members"]]
        self.assertIn("some hint text", labels0)
        self.assertNotIn("Option Alpha", labels0)
        labels1 = [m.get("label") for m in blocks[1]["members"]]
        self.assertIn("Option Alpha", labels1)

    def test_hint_includes_title_and_extras(self):
        blocks = bd._autopilot_blocks(self._panel())
        self.assertIn("first question title", blocks[0]["hint"])
        self.assertIn("some hint text", blocks[0]["hint"])

    def test_no_markers_no_blocks(self):
        self.assertEqual(bd._autopilot_blocks([w(label="just text", x=500)]), [])


class TestErrorDetection(unittest.TestCase):
    def test_detects_inline_validation_errors(self):
        for msg in ("Please enter a valid number", "Invalid value",
                    "This field is required", "Value must be positive"):
            blk = {"members": [w(label="Weight"), w(label=msg)]}
            self.assertTrue(bd._block_has_error(blk), msg)

    def test_clean_block(self):
        blk = {"members": [w(label="Weight"), w(role="textbox", value="55")]}
        self.assertFalse(bd._block_has_error(blk))


class TestWidgetSig(unittest.TestCase):
    def test_quantizes_position(self):
        a = w(role="button", label="X", x=100, y=200)
        b = w(role="button", label="X", x=104, y=204)   # same 25px bucket
        c = w(role="button", label="X", x=160, y=200)
        self.assertEqual(bd._widget_sig(a), bd._widget_sig(b))
        self.assertNotEqual(bd._widget_sig(a), bd._widget_sig(c))


class TestSlimWidgets(unittest.TestCase):
    def test_shape(self):
        slim = bd._slim_widgets([w(role="button", label="Go", value="checked", x=10, y=20)])
        self.assertEqual(slim, [{"role": "button", "x": 10, "y": 20,
                                 "label": "Go", "value": "checked"}])


def _make_png(pixels, width, height):
    """Encodes an RGBA PNG (filter 0 rows) from a flat [(r,g,b,a), ...] list."""
    raw = b""
    for y in range(height):
        raw += b"\x00"
        for x in range(width):
            raw += bytes(pixels[y * width + x])
    def chunk(typ, data):
        c = typ + data
        return struct.pack(">I", len(data)) + c + struct.pack(">I", zlib.crc32(c))
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw))
            + chunk(b"IEND", b""))


class TestPngDecoder(unittest.TestCase):
    def test_roundtrip_rgba(self):
        pixels = [(255, 0, 0, 255), (0, 255, 0, 255),
                  (0, 0, 255, 255), (10, 20, 30, 255)]
        data = _make_png(pixels, 2, 2)
        w_, h, ch, out = bd._png_pixels(data)
        self.assertEqual((w_, h, ch), (2, 2, 4))
        got = [(out[i], out[i + 1], out[i + 2], out[i + 3])
               for i in range(0, len(out), 4)]
        self.assertEqual(got, pixels)


class TestAnswerValuePriority(unittest.TestCase):
    ARGS = dict(text_value="test", number_value="55",
                today="8/30/2026", now_hhmm="14:05")

    def val(self, hint, numeric_error=False, attempt=1):
        return bd._autopilot_answer_value(hint, numeric_error, attempt, **self.ARGS)

    def test_plain_text(self):
        self.assertEqual(self.val("enter participant name"), "test")

    def test_number_hints_without_a_stated_range_keep_the_flat_default(self):
        for hint in ("weight", "heart rate", "how many pills"):
            self.assertEqual(self.val(hint), "55", hint)

    def test_number_hint_with_a_stated_range_is_answer_correct_not_flat(self):
        # "Pain score (0-10)" typed as the flat default '55' is WRONG data —
        # this is exactly the bug the answer-correctness checker exists to
        # fix: the default must actually satisfy the question's own range.
        self.assertEqual(self.val("pain score (0-10)"), "5")

    def test_date_and_time(self):
        self.assertEqual(self.val("visit date"), "8/30/2026")
        self.assertEqual(self.val("visit time"), "14:05")

    def test_retry_forces_number_for_plain_text(self):
        self.assertEqual(self.val("enter the code", attempt=2), "55")

    def test_numeric_rejection_wins_even_over_date(self):
        # "a date landed in a number field" — the explicit rejection decides.
        self.assertEqual(self.val("measurement date", numeric_error=True), "55")

    def test_generic_error_on_date_field_still_gets_a_date(self):
        # An "is required" error on a date question must retype the DATE,
        # never fall through to the number default.
        self.assertEqual(self.val("visit date", numeric_error=False, attempt=3),
                         "8/30/2026")
        self.assertEqual(self.val("visit time", numeric_error=False, attempt=3),
                         "14:05")

    def test_birth_date_question_never_gets_todays_date(self):
        self.assertEqual(
            bd._autopilot_answer_value("date of birth", False, 1, **self.ARGS),
            "1/1/1990")

    def test_expiry_date_question_gets_a_future_date_not_today(self):
        self.assertEqual(
            bd._autopilot_answer_value("consent expiry date", False, 1,
                                       **self.ARGS, expiry_date="1/1/2028"),
            "1/1/2028")

    def test_generic_visit_date_unaffected_by_smart_date_keywords(self):
        self.assertEqual(self.val("actual visit date"), "8/30/2026")

    def test_nearby_note_text_never_redirects_an_ordinary_date_field(self):
        # The hint (title + nearby helper-widget text) contains "expired",
        # but the question's own TITLE is just "assessment date" — the
        # unrelated note must not redirect the answer away from today. A
        # distinct expiry_date proves this: if the note text were (wrongly)
        # matched, the answer would come back as "1/1/2028", not today.
        hint = "assessment date note: expired consent version 2 on file"
        value = bd._autopilot_answer_value(
            hint, False, 1, title="assessment date", expiry_date="1/1/2028",
            **self.ARGS)
        self.assertEqual(value, "8/30/2026")

    def test_composite_numeric_block_keeps_flat_default_for_every_box(self):
        # A block with TWO empty number boxes sharing one hint (e.g. a
        # Systolic/Diastolic vitals question) must never have a range
        # stitched from one field's bound to the other's and typed into
        # both — single_field=False disables range-aware numbers entirely,
        # matching the old flat-default behavior for composite fields.
        hint = "systolic (min 90) and diastolic (max 120) blood pressure"
        value = bd._autopilot_answer_value(
            hint, False, 1, single_field=False, **self.ARGS)
        self.assertEqual(value, "55")

    def test_single_field_block_still_gets_the_range_aware_value(self):
        # Same range-bearing hint, but single_field=True (the default,
        # exactly one empty box in the block) — the smart value still fires.
        hint = "pain score (0-10)"
        value = bd._autopilot_answer_value(
            hint, False, 1, single_field=True, **self.ARGS)
        self.assertEqual(value, "5")


class TestRangeExtraction(unittest.TestCase):
    def test_parenthesized_range(self):
        self.assertEqual(bd._autopilot_extract_range("pain score (0-10)"), (0.0, 10.0))
        self.assertEqual(bd._autopilot_extract_range("scale (1 to 5)"), (1.0, 5.0))
        self.assertEqual(bd._autopilot_extract_range("intensity (0 – 4)"), (0.0, 4.0))

    def test_between_phrasing(self):
        self.assertEqual(bd._autopilot_extract_range("enter a value between 1 and 100"),
                         (1.0, 100.0))

    def test_min_max_either_order(self):
        self.assertEqual(bd._autopilot_extract_range("min 2 max 8"), (2.0, 8.0))
        self.assertEqual(bd._autopilot_extract_range("maximum: 8 minimum: 2"), (2.0, 8.0))

    def test_upper_bound_only_assumes_zero_floor(self):
        self.assertEqual(bd._autopilot_extract_range("up to 20 tablets"), (0.0, 20.0))
        self.assertEqual(bd._autopilot_extract_range("no more than 50"), (0.0, 50.0))

    def test_lower_bound_only_has_no_ceiling(self):
        self.assertEqual(bd._autopilot_extract_range("at least 10 years old"), (10.0, None))

    def test_no_range_stated_returns_none(self):
        for hint in ("weight", "enter participant name", "visit date", "how many pills"):
            self.assertIsNone(bd._autopilot_extract_range(hint), hint)

    def test_reversed_bounds_are_normalized(self):
        # a hostile/odd phrasing shouldn't produce lo > hi
        rng = bd._autopilot_extract_range("min 8 max 2")
        self.assertEqual(rng, (2.0, 8.0))


class TestSmartNumber(unittest.TestCase):
    def test_ranged_hint_picks_midpoint_not_flat_default(self):
        self.assertEqual(bd._autopilot_smart_number("pain score (0-10)", "55"), "5")

    def test_flat_default_already_in_range_is_kept(self):
        # no pointless churn when '55' already satisfies the question.
        self.assertEqual(bd._autopilot_smart_number("scale (0-100)", "55"), "55")

    def test_no_range_leaves_flat_default_untouched(self):
        self.assertEqual(bd._autopilot_smart_number("weight", "55"), "55")

    def test_single_value_range_uses_that_value(self):
        self.assertEqual(bd._autopilot_smart_number("fixed dose (5-5)", "55"), "5")

    def test_lower_bound_only_bumps_default_up_when_below_it(self):
        self.assertEqual(bd._autopilot_smart_number("at least 100 mg", "55"), "100")

    def test_lower_bound_only_keeps_default_when_already_satisfied(self):
        self.assertEqual(bd._autopilot_smart_number("at least 10 mg", "55"), "55")

    def test_midpoint_is_a_whole_number_string(self):
        v = bd._autopilot_smart_number("scale (0-10)", "55")
        self.assertNotIn(".", v)


class TestSmartDate(unittest.TestCase):
    TODAY = "8/30/2026"

    def test_birth_keywords(self):
        for hint in ("date of birth", "birth date", "birthdate", "dob"):
            self.assertEqual(bd._autopilot_smart_date(hint, self.TODAY), "1/1/1990", hint)

    def test_expiry_keywords_use_expiry_date_not_today(self):
        for hint in ("expiry date", "expiration date", "consent expires on"):
            self.assertEqual(
                bd._autopilot_smart_date(hint, self.TODAY, expiry_date="1/1/2028"),
                "1/1/2028", hint)

    def test_expiry_without_explicit_expiry_date_falls_back_to_today(self):
        self.assertEqual(bd._autopilot_smart_date("expiry date", self.TODAY), self.TODAY)

    def test_generic_date_hints_unaffected(self):
        for hint in ("visit date", "assessment date", "date collected"):
            self.assertEqual(bd._autopilot_smart_date(hint, self.TODAY), self.TODAY, hint)

    def test_custom_birth_date_override(self):
        self.assertEqual(
            bd._autopilot_smart_date("date of birth", self.TODAY, birth_date="6/15/1985"),
            "6/15/1985")

    def test_dob_never_matches_inside_a_larger_word(self):
        # "dob" is a real word-fragment inside unrelated words — must not
        # fire on a generic date question just because a drug name or an
        # app name happens to contain the letters "d-o-b".
        for hint in ("date scanned via adobe reader",
                     "administered dobutamine dose",
                     "date recorded"):
            self.assertEqual(bd._autopilot_smart_date(hint, self.TODAY), self.TODAY, hint)

    def test_expir_never_matches_unrelated_words(self):
        for hint in ("date experience began", "inspiration date",
                     "date of expert review"):
            self.assertEqual(bd._autopilot_smart_date(hint, self.TODAY), self.TODAY, hint)


class TestDialogAcceptLabels(unittest.TestCase):
    def test_accept_list_is_exact_lowercase(self):
        # The accept scan uses exact lowercase equality — a drive-by rename to
        # substring matching would make it click things like "Select all…".
        for lbl in bd._AUTOPILOT_DIALOG_ACCEPT:
            self.assertEqual(lbl, lbl.strip().lower())


if __name__ == "__main__":
    unittest.main()
