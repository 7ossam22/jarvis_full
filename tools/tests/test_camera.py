#!/usr/bin/env python3
"""Camera routing and the image path into a model call.

Two cameras told apart by one word, and a picture that has to survive the trip
from a webcam or a browser canvas into three differently-shaped provider
payloads. The failure that matters most is silent: a turn that carries no
frame but still gets answered as though it did.
"""
import base64
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from app import turn, vision                                    # noqa: E402
from app.providers.gemini_provider import _to_gemini_contents    # noqa: E402
from app.providers.lmstudio_provider import _to_openai_messages  # noqa: E402

JPEG_B64 = base64.b64encode(b"\xff\xd8\xff\xe0" + b"x" * 200).decode()


class CameraIntent(unittest.TestCase):
    def tearDown(self):
        turn.bind_message("")

    def test_remote_means_the_machine(self):
        for msg in ("open remote camera", "open the remote camera please",
                    "open the machine camera", "what does the camera on the machine see"):
            self.assertTrue(turn.wants_remote_camera(msg), msg)
            # "remote" must never also trigger the browser camera.
            self.assertFalse(turn.wants_to_be_seen(msg), msg)

    def test_looking_means_this_device(self):
        for msg in ("look at me", "open your eyes", "what do you see right now",
                    "can you see me?", "take a look", "turn on the camera"):
            self.assertTrue(turn.wants_to_be_seen(msg), msg)
            self.assertFalse(turn.wants_remote_camera(msg), msg)

    def test_closing(self):
        for msg in ("close your eyes", "stop looking", "turn off the camera"):
            self.assertTrue(turn.wants_eyes_closed(msg), msg)

    def test_unrelated_requests_open_nothing(self):
        for msg in ("open novatek", "show me the LoL site", "play unfold",
                    "what is the weather", "close the browser tab"):
            self.assertFalse(turn.wants_to_be_seen(msg), msg)
            self.assertFalse(turn.wants_remote_camera(msg), msg)


class VisionRouting(unittest.TestCase):
    """A frame may only go to a backend that can actually look at it.

    Observed live: model.provider was "anthropic" with no API key, so the turn
    went to the `claude -p` fallback, which flattens messages to text. The
    frame vanished, the model saw the "[live frame...]" note with no picture,
    and told the user the camera had failed.
    """

    def setUp(self):
        from app.providers import llm
        self.llm = llm
        self._real = llm.get_llm_providers
        self.asked = []

        outer = self

        class Fake(llm.LLMProvider):
            def __init__(self, name, sees):
                self.name, self._sees = name, sees

            def is_configured(self):
                return True

            def supports_vision(self):
                return self._sees

            def converse(self, system_prompt, messages):
                outer.asked.append(self.name)
                return f"answer from {self.name}"

        self.blind = Fake("claude-cli", False)
        self.seeing = Fake("gemini", True)

    def tearDown(self):
        self.llm.get_llm_providers = self._real

    def _use(self, *providers):
        self.llm.get_llm_providers = lambda cfg, prefer=None: list(providers)

    def test_blind_backend_is_skipped_not_merely_deprioritised(self):
        self._use(self.blind, self.seeing)
        out = self.llm.call_model(None, "S", [{"role": "user", "content": "?"}],
                                  "fb", needs_vision=True)
        self.assertEqual(self.asked, ["gemini"])
        self.assertEqual(out, "answer from gemini")

    def test_text_turns_keep_the_normal_order(self):
        self._use(self.blind, self.seeing)
        self.llm.call_model(None, "S", [{"role": "user", "content": "hi"}],
                            "fb", needs_vision=False)
        self.assertEqual(self.asked, ["claude-cli"])

    def test_no_seeing_backend_says_so_instead_of_answering_blind(self):
        self._use(self.blind)
        out = self.llm.call_model(None, "S", [{"role": "user", "content": "?"}],
                                  "fb", needs_vision=True)
        self.assertEqual(self.asked, [])
        self.assertIn("look at an image", out)

    def test_vision_provider_picks_who_does_the_looking(self):
        """model.vision_provider must steer vision independently of
        model.provider. assist_provider does NOT do this — it governs only the
        form-autopilot escalation — and reaching for it here silently changed
        nothing."""
        self._use(self.seeing, Fake_ := self.blind)      # order is irrelevant
        from app.providers import llm
        captured = {}
        real = llm.get_llm_providers

        def spy(cfg, prefer=None):
            captured["prefer"] = prefer
            return [self.seeing]

        llm.get_llm_providers = spy
        try:
            llm.call_model(None, "S", [{"role": "user", "content": "?"}],
                           "fb", needs_vision=True, prefer="lmstudio")
        finally:
            llm.get_llm_providers = real
        self.assertEqual(captured["prefer"], "lmstudio")

    def test_claude_cli_refuses_an_image_turn(self):
        from app.providers import anthropic_provider
        msgs = [{"role": "user", "content": "?",
                 "images": [{"media_type": "image/jpeg", "data": JPEG_B64}]}]
        with self.assertRaises(RuntimeError) as ctx:
            anthropic_provider.call_claude_cli(None, "S", msgs)
        self.assertIn("text-only", str(ctx.exception))


class Normalization(unittest.TestCase):
    """Frames arrive from a browser canvas and a webcam driver — untrusted
    input. A bad frame must cost the picture, never the whole answer."""

    def test_accepts_the_shapes_that_actually_arrive(self):
        self.assertEqual(len(vision.normalize_images(JPEG_B64)), 1)
        self.assertEqual(len(vision.normalize_images(f"data:image/jpeg;base64,{JPEG_B64}")), 1)
        self.assertEqual(len(vision.normalize_images(
            {"media_type": "image/png", "data": JPEG_B64})), 1)

    def test_data_url_media_type_is_read_from_the_header(self):
        got = vision.normalize_images(f"data:image/webp;base64,{JPEG_B64}")
        self.assertEqual(got[0]["media_type"], "image/webp")

    def test_rejects_junk_without_raising(self):
        for bad in ("not!!base64", {"media_type": "application/pdf", "data": JPEG_B64},
                    {"data": ""}, None, [], 42, "data:image/jpeg;base64,"):
            self.assertEqual(vision.normalize_images(bad), [], repr(bad))

    def test_caps_count_and_size(self):
        self.assertEqual(len(vision.normalize_images([JPEG_B64] * 9)), vision.MAX_IMAGES)
        huge = base64.b64encode(b"x" * (vision.MAX_IMAGE_BYTES + 1)).decode()
        self.assertEqual(vision.normalize_images(huge), [])


class ProviderShapes(unittest.TestCase):
    def setUp(self):
        self.msgs = [{"role": "user", "content": "what do you see?",
                      "images": [{"media_type": "image/jpeg", "data": JPEG_B64}]}]

    def test_gemini_inline_data_before_text(self):
        parts = _to_gemini_contents(self.msgs)[0]["parts"]
        self.assertEqual([list(p)[0] for p in parts], ["inline_data", "text"])

    def test_openai_image_url_is_a_data_url(self):
        content = _to_openai_messages("SYS", self.msgs)[-1]["content"]
        self.assertEqual(content[0]["type"], "image_url")
        self.assertTrue(content[0]["image_url"]["url"].startswith("data:image/jpeg;base64,"))

    def test_anthropic_blocks(self):
        blocks = vision.to_anthropic_content("what do you see?",
                                             vision.images_of(self.msgs[0]))
        self.assertEqual([b["type"] for b in blocks], ["image", "text"])
        self.assertEqual(blocks[0]["source"]["type"], "base64")

    def test_textonly_turns_are_byte_identical_to_before(self):
        """The whole app runs through these converters; a turn with no camera
        must not start sending a different payload shape."""
        plain = [{"role": "user", "content": "hello"}]
        self.assertEqual(_to_gemini_contents(plain),
                         [{"role": "user", "parts": [{"text": "hello"}]}])
        self.assertEqual(_to_openai_messages("SYS", plain)[1],
                         {"role": "user", "content": "hello"})
        self.assertEqual(vision.to_anthropic_content("hello", []), "hello")


class MachineCamera(unittest.TestCase):
    """Runs against whatever hardware is present; asserts only what must hold
    either way, so this passes on a machine with no camera at all."""

    def test_capture_never_raises(self):
        from app.connectors import camera
        res = camera.capture_frame(device="/dev/does-not-exist")
        self.assertFalse(res["ok"])
        self.assertIn("does not exist", res["error"])

    def test_real_capture_is_a_valid_jpeg_if_a_camera_exists(self):
        from app.connectors import camera
        devices = camera.list_devices()
        if not devices:
            self.skipTest("no capture-capable camera on this machine")
        res = camera.capture_frame()
        if not res["ok"]:
            self.skipTest(f"camera present but unavailable: {res['error']}")
        self.assertTrue(res["jpeg"].startswith(b"\xff\xd8"))   # SOI
        self.assertTrue(res["jpeg"].rstrip(b"\x00").endswith(b"\xff\xd9"))  # EOI
        self.assertGreater(res["bytes"], 1024)


if __name__ == "__main__":
    unittest.main(verbosity=2)
