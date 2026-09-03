#!/usr/bin/env python3
"""More than one Novatek deployment.

The portal was a single hardcoded URL in five places — the persona rules, the
browser display policy, the retrieval notes, the README and the tests. These
lock down the replacement: one site map in config, everything else reading it.

Getting the site wrong is not a cosmetic error. The automation logs in and
starts filling real forms, so an unknown name must be refused rather than
quietly resolved to the default.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from app import turn                      # noqa: E402
from app.config import Config             # noqa: E402


class SiteFromCommand(unittest.TestCase):
    def tearDown(self):
        turn.bind_message("")

    def test_named_after_novatek(self):
        for msg, want in (("open novatek hcc", "hcc"), ("Open novatek nec", "nec"),
                          ("open novatek-hcc", "hcc"), ("open novatek: hcc", "hcc"),
                          ("launch novatek hcc now", "hcc"),
                          ("go to novatek nec and log in", "nec")):
            self.assertEqual(turn.novatek_site_key(msg), want, msg)

    def test_named_before_novatek(self):
        self.assertEqual(turn.novatek_site_key("open the hcc novatek"), "hcc")

    def test_no_name_means_the_default(self):
        for msg in ("open novatek", "open novatek please", "open novatek portal",
                    "open novatek and start a visit", "launch novatek"):
            self.assertIsNone(turn.novatek_site_key(msg), msg)

    def test_a_site_name_only_counts_beside_novatek(self):
        # REGRESSION: one regex with an alternation, scanned left to right,
        # matched "open novatek" on the before-branch, consumed the word, and
        # never saw the "hcc" that followed.
        self.assertEqual(turn.novatek_site_key("open novatek hcc"), "hcc")
        # And an unrelated "nec" must not select a portal.
        self.assertIsNone(turn.novatek_site_key("show me the nec site"))
        self.assertIsNone(turn.novatek_site_key("play unfold"))

    def test_unknown_names_are_returned_not_swallowed(self):
        """The caller reports them; defaulting on a typo opens the wrong trial."""
        self.assertEqual(turn.novatek_site_key("open novatek hccc"), "hccc")


class SiteMap(unittest.TestCase):
    def setUp(self):
        self.cfg = Config.load()

    def test_both_deployments_resolve(self):
        self.assertEqual(self.cfg.novatek_site("nec")["url"],
                         "https://nec-dev.autotrial.app")
        self.assertEqual(self.cfg.novatek_site("hcc")["url"],
                         "https://hcc-dev.autotrial.app")

    def test_case_insensitive(self):
        self.assertEqual(self.cfg.novatek_site("HCC")["url"],
                         self.cfg.novatek_site("hcc")["url"])

    def test_unknown_site_is_none(self):
        self.assertIsNone(self.cfg.novatek_site("zzz"))

    def test_default_site(self):
        self.assertEqual(self.cfg.novatek_site()["url"],
                         "https://nec-dev.autotrial.app")

    def test_hosts_cover_every_site(self):
        hosts = self.cfg.novatek_hosts()
        self.assertIn("nec-dev.autotrial.app", hosts)
        self.assertIn("hcc-dev.autotrial.app", hosts)

    def test_credentials_fall_back_to_the_shared_pair(self):
        shared_user, _ = self.cfg.novatek_credentials()
        if not shared_user:
            self.skipTest("no Novatek credentials configured on this machine")
        for key in ("nec", "hcc"):
            self.assertEqual(self.cfg.novatek_site(key)["username"], shared_user)


class DisplayPolicyExemption(unittest.TestCase):
    """Every portal is an application to operate, so none may be forced into
    the embedded viewer — including one added after this code was written."""

    def setUp(self):
        self.cfg = Config.load()

    def tearDown(self):
        turn.bind_message("")

    def test_every_configured_host_is_exempt(self):
        turn.bind_message("show me the novatek dashboard")
        hosts = self.cfg.novatek_hosts()
        for site in self.cfg.novatek_sites().values():
            self.assertIsNone(
                turn.browser_policy_violation(site["url"] + "/visit",
                                              automation_hosts=hosts),
                site["url"])


class OpenTool(unittest.TestCase):
    def setUp(self):
        from app.connectors import browser
        self.browser = browser
        self.cfg = Config.load()
        self._daemon, self._request = browser._ensure_daemon, browser._daemon_request
        browser._ensure_daemon = lambda: None
        self.sent = {}
        browser._daemon_request = lambda path, payload: self.sent.update(payload) or {"status": "opened"}

    def tearDown(self):
        self.browser._ensure_daemon = self._daemon
        self.browser._daemon_request = self._request

    def _open(self, site=None):
        return self.browser.execute_browser_tool(
            self.cfg, "novatek_open", {"site": site} if site else {})

    def test_resolves_each_site(self):
        self._open("hcc")
        self.assertEqual(self.sent["url"], "https://hcc-dev.autotrial.app")
        self._open("nec")
        self.assertEqual(self.sent["url"], "https://nec-dev.autotrial.app")

    def test_no_site_uses_the_default(self):
        self._open()
        self.assertEqual(self.sent["url"], "https://nec-dev.autotrial.app")

    def test_unknown_site_is_refused_by_name(self):
        self.sent.clear()
        res = self._open("zzz")
        self.assertEqual(self.sent, {}, "must not open anything")
        self.assertIn("zzz", res["error"])
        self.assertIn("nec", res["error"])       # lists the real ones

    def test_registered_for_the_model(self):
        from app.connectors.registry import registry
        self.assertIn("novatek_open", registry.names())


class PersonaText(unittest.TestCase):
    """The rules the model reads must be generated from the site map, not
    written by hand — that is how the second deployment got missed."""

    def test_lists_every_site_and_no_stale_url(self):
        from app import persona
        prompt = persona.build_system_prompt(Config.load())
        self.assertIn("novatek_open", prompt)
        for host in Config.load().novatek_hosts():
            self.assertIn(host, prompt)
        self.assertNotIn("Navigate to `https://nec-dev.autotrial.app`", prompt)


if __name__ == "__main__":
    unittest.main(verbosity=2)
