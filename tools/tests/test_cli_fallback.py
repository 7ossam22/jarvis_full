"""tools/tests/test_cli_fallback.py — Unit tests for CLI provider & configurable fallback."""
import unittest
from unittest.mock import MagicMock, patch

from app.config import Config
from app.providers.cli_provider import (
    CLI_AGY,
    CLI_CLAUDE,
    CLI_NONE,
    CLIProvider,
    is_cli_available,
    normalize_cli_name,
    resolve_cli_candidates,
    call_cli_fallback,
    call_cli_turn,
)
from app.providers.llm import get_llm_providers
from app.providers.gemini_provider import GeminiProvider
from app.providers.anthropic_provider import AnthropicProvider


class CLIFallbackTests(unittest.TestCase):

    def test_normalize_cli_name(self):
        self.assertEqual(normalize_cli_name("agy"), "agy")
        self.assertEqual(normalize_cli_name("AGY"), "agy")
        self.assertEqual(normalize_cli_name("antigravity"), "agy")
        self.assertEqual(normalize_cli_name("claude"), "claude")
        self.assertEqual(normalize_cli_name("off"), "none")
        self.assertEqual(normalize_cli_name("disabled"), "none")
        self.assertEqual(normalize_cli_name(None), "")

    def test_resolve_cli_candidates_none(self):
        cfg = Config({"model": {"cli_fallback": "none"}})
        self.assertEqual(resolve_cli_candidates(cfg), [])

    @patch("app.providers.cli_provider.is_cli_installed", side_effect=lambda name: True)
    def test_resolve_cli_candidates_explicit_agy(self, _mock_installed):
        cfg = Config({"model": {"cli_fallback": "agy"}})
        cands = resolve_cli_candidates(cfg)
        self.assertEqual(cands[0], CLI_AGY)
        self.assertIn(CLI_CLAUDE, cands)

    @patch("app.providers.cli_provider.is_cli_installed", side_effect=lambda name: True)
    def test_resolve_cli_candidates_explicit_claude(self, _mock_installed):
        cfg = Config({"model": {"cli_fallback": "claude"}})
        cands = resolve_cli_candidates(cfg)
        self.assertEqual(cands[0], CLI_CLAUDE)
        self.assertIn(CLI_AGY, cands)

    @patch("app.providers.cli_provider.is_cli_installed", side_effect=lambda name: True)
    def test_resolve_cli_candidates_auto_preferred(self, _mock_installed):
        cfg = Config({"model": {"cli_fallback": "auto"}})
        self.assertEqual(resolve_cli_candidates(cfg, preferred="claude")[0], CLI_CLAUDE)
        self.assertEqual(resolve_cli_candidates(cfg, preferred="agy")[0], CLI_AGY)

    @patch("app.providers.cli_provider.is_cli_installed", side_effect=lambda name: False)
    def test_is_cli_available_when_nothing_installed(self, _mock_installed):
        cfg = Config({"model": {"cli_fallback": "auto"}})
        self.assertFalse(is_cli_available(cfg))

    def test_vision_refusal(self):
        import base64
        jpeg_b64 = base64.b64encode(b"\xff\xd8\xff\xe0" + b"x" * 200).decode()
        msgs = [{"role": "user", "content": "?", "images": [{"media_type": "image/jpeg", "data": jpeg_b64}]}]
        with self.assertRaises(RuntimeError) as ctx:
            call_cli_turn(None, "Sys", msgs, cli_name="agy")
        self.assertIn("text-only", str(ctx.exception))
        self.assertIn("agy", str(ctx.exception))

    @patch("app.providers.cli_provider.call_cli_turn")
    def test_fallback_failover_between_clis(self, mock_turn):
        # First candidate fails, second candidate succeeds
        mock_turn.side_effect = [RuntimeError("Claude session limit hit"), "Hello from agy!"]
        cfg = Config({"model": {"cli_fallback": "claude"}})
        with patch("app.providers.cli_provider.is_cli_installed", return_value=True):
            res = call_cli_fallback(cfg, "Sys", [{"role": "user", "content": "hi"}], preferred="claude")
            self.assertEqual(res, "Hello from agy!")
            self.assertEqual(mock_turn.call_count, 2)

    @patch("app.providers.cli_provider.is_cli_installed", return_value=True)
    def test_cli_provider_class(self, _mock_installed):
        p = CLIProvider(None, cli_type="agy")
        self.assertEqual(p.name, "agy")
        self.assertTrue(p.is_configured())
        self.assertFalse(p.supports_vision())

    @patch("app.providers.cli_provider.is_cli_installed", return_value=True)
    def test_get_llm_providers_prioritizes_agy_when_chosen(self, _mock_installed):
        cfg = Config({"model": {"provider": "agy"}})
        providers = get_llm_providers(cfg)
        self.assertTrue(len(providers) > 0)
        self.assertEqual(providers[0].name, "agy")

    @patch("app.providers.gemini_provider.is_cli_available", return_value=True)
    @patch("app.providers.gemini_provider.call_cli_fallback", return_value="gemini cli response")
    def test_gemini_provider_falls_back_to_cli(self, mock_call, _mock_avail):
        cfg = Config({"model": {"gemini_api_key": ""}})
        gp = GeminiProvider(cfg)
        self.assertTrue(gp.is_configured())
        self.assertFalse(gp.supports_vision())
        reply = gp.converse("Sys", [{"role": "user", "content": "hi"}])
        self.assertEqual(reply, "gemini cli response")
        mock_call.assert_called_once()


if __name__ == "__main__":
    unittest.main()
