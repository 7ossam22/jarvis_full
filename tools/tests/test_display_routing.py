#!/usr/bin/env python3
"""Where a request gets displayed: in-interface by default, real browser only
when asked for or when nothing built in can show it.

The prompt asks for this too, but a prompt is advice — the model opened the
machine browser for "play X on YouTube" anyway. These lock down the part that
is enforced rather than requested.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from app import turn  # noqa: E402


class BuiltinHandler(unittest.TestCase):
    """Must claim a URL only when the interface can really render it —
    a refusal that promises a blank window is worse than no refusal."""

    def test_youtube_video_ids(self):
        for url in ("https://www.youtube.com/watch?v=kJQP7ciFHFo",
                    "https://youtu.be/kJQP7ciFHFo",
                    "https://www.youtube.com/shorts/kJQP7ciFHFo",
                    "https://www.youtube.com/embed/kJQP7ciFHFo"):
            self.assertEqual(turn.builtin_handler_for(url), "video", url)

    def test_youtube_without_a_video_is_not_claimed(self):
        # No id to embed, and YouTube refuses iframing — the browser is right.
        for url in ("https://www.youtube.com", "https://www.youtube.com/",
                    "https://www.youtube.com/feed/trending"):
            self.assertIsNone(turn.builtin_handler_for(url), url)

    def test_direct_media(self):
        self.assertEqual(turn.builtin_handler_for("https://x/clip.mp4"), "video")
        self.assertEqual(turn.builtin_handler_for("https://x/clip.webm?t=1"), "video")
        self.assertEqual(turn.builtin_handler_for("https://x/cat.JPG"), "image")

    def test_unhandled_targets(self):
        for url in ("https://nec-dev.autotrial.app", "https://en.wikipedia.org/wiki/Cairo",
                    "https://x/a.mov", "", None, 42):
            self.assertIsNone(turn.builtin_handler_for(url), url)


class BrowserKeyword(unittest.TestCase):
    def test_naming_the_browser_opts_out(self):
        for msg in ("open youtube in the browser", "open chrome", "open it in a new tab",
                    "use chromium", "open incognito"):
            self.assertTrue(turn.asked_for_browser(msg), msg)

    def test_ordinary_requests_do_not(self):
        for msg in ("play despacito on youtube", "show me the cairo page",
                    "open novatek", "play that clip"):
            self.assertFalse(turn.asked_for_browser(msg), msg)


class Policy(unittest.TestCase):
    def tearDown(self):
        turn.bind_message("")

    def test_blocks_browser_for_in_app_media(self):
        turn.bind_message("play despacito on youtube")
        msg = turn.browser_policy_violation("https://youtu.be/kJQP7ciFHFo")
        self.assertIsNotNone(msg)
        self.assertIn("VIDEO:", msg)          # tells the model what to do instead

    def test_allows_when_user_named_the_browser(self):
        turn.bind_message("open youtube in the browser")
        self.assertIsNone(turn.browser_policy_violation("https://youtu.be/kJQP7ciFHFo"))

    def test_allows_targets_with_no_builtin_handler(self):
        turn.bind_message("open novatek")
        self.assertIsNone(turn.browser_policy_violation("https://nec-dev.autotrial.app"))

    def test_unbound_turn_enforces_nothing(self):
        # The autopilot, the CLI and these tests drive a real browser on
        # purpose; there is no user request to interpret, so no policy.
        turn.bind_message("")
        self.assertIsNone(turn.browser_policy_violation("https://youtu.be/kJQP7ciFHFo"))


class PlaybackIntent(unittest.TestCase):
    """"Play X" must never be answered with a browser window. Observed live:
    the model could not find the track, opened youtube.com/results in the real
    browser, and reported success — the URL is not a playable video so the
    URL-shaped check alone passed it through."""

    def tearDown(self):
        turn.bind_message("")

    def test_search_page_refused_when_asked_to_play(self):
        turn.bind_message("Travis play unfold for Porter Robinson on YouTube")
        msg = turn.browser_policy_violation(
            "https://www.youtube.com/results?search_query=Porter+Robinson+Unfold")
        self.assertIsNotNone(msg)
        self.assertIn("web_search", msg)   # tells it how to find the real video

    def test_channel_and_homepage_refused_too(self):
        turn.bind_message("play some porter robinson")
        for url in ("https://www.youtube.com", "https://www.youtube.com/@porterrobinson",
                    "https://www.youtube.com/feed/trending"):
            self.assertIsNotNone(turn.browser_policy_violation(url), url)

    def test_browser_keyword_still_wins(self):
        turn.bind_message("play unfold on youtube in the browser")
        self.assertIsNone(turn.browser_policy_violation(
            "https://www.youtube.com/results?search_query=unfold"))

    def test_non_video_host_unaffected(self):
        # "watch" appears, but this is not a video site — the browser is a
        # legitimate fallback and must not be blocked.
        turn.bind_message("watch the build output on the CI page")
        self.assertIsNone(turn.browser_policy_violation("https://ci.example.com/job/42"))

    def test_open_without_playback_intent_unaffected(self):
        turn.bind_message("open novatek and start a visit")
        self.assertIsNone(turn.browser_policy_violation("https://nec-dev.autotrial.app"))

    def test_intent_words(self):
        for msg in ("play unfold", "watch the trailer", "listen to this", "stream it"):
            self.assertTrue(turn.wants_playback(msg), msg)
        for msg in ("display the results", "open the playstore", "replay the log",
                    "open novatek"):
            self.assertFalse(turn.wants_playback(msg), msg)


class DisplayIntent(unittest.TestCase):
    """"Show me X" must land in the embedded viewer. Observed live: "show me
    the official website for League of Legends" opened the machine browser,
    because the URL alone looks like any other page — only the request reveals
    where it belongs."""

    def tearDown(self):
        turn.bind_message("")

    def test_show_me_a_site_is_refused(self):
        turn.bind_message("show me the official website for League of Legends")
        msg = turn.browser_policy_violation("https://www.leagueoflegends.com")
        self.assertIsNotNone(msg)
        self.assertIn("SHOW:", msg)

    def test_image_requests_redirect_to_image_lines(self):
        turn.bind_message("bring me a couple of images of porter robinson")
        msg = turn.browser_policy_violation("https://www.google.com/search?q=x&tbm=isch")
        self.assertIsNotNone(msg)
        self.assertIn("IMAGE:", msg)

    def test_browser_keyword_still_wins(self):
        turn.bind_message("show me the league of legends site in the browser")
        self.assertIsNone(turn.browser_policy_violation("https://www.leagueoflegends.com"))

    def test_automation_hosts_are_exempt(self):
        # An embedded viewer cannot log into and drive a Flutter app; refusing
        # the real browser here would break the workflow this app exists for.
        turn.bind_message("show me the novatek dashboard")
        self.assertIsNone(turn.browser_policy_violation("https://nec-dev.autotrial.app/x"))

    def test_configured_automation_hosts_are_honoured(self):
        turn.bind_message("show me the internal portal")
        url = "https://portal.internal.example/x"
        self.assertIsNotNone(turn.browser_policy_violation(url))
        self.assertIsNone(turn.browser_policy_violation(
            url, automation_hosts=("portal.internal.example",)))

    def test_no_display_intent_is_left_alone(self):
        # No show/play verb: the browser stays a legitimate fallback.
        turn.bind_message("log into the admin panel and check the queue")
        self.assertIsNone(turn.browser_policy_violation("https://admin.example.com"))

    def test_intent_words(self):
        for msg in ("show me the site", "pull up the page", "let me see it",
                    "bring me images", "look at this page", "display the chart"):
            self.assertTrue(turn.wants_display(msg), msg)
        for msg in ("open novatek", "log in and fill the form", "run the test"):
            self.assertFalse(turn.wants_display(msg), msg)


class Connector(unittest.TestCase):
    """The guard has to sit in the tool's real path, not just in the helper."""

    def setUp(self):
        from app.connectors import browser
        self.browser = browser
        self._real = browser._ensure_daemon
        browser._ensure_daemon = lambda: "daemon not started (test)"

    def tearDown(self):
        self.browser._ensure_daemon = self._real
        turn.bind_message("")

    def _refused(self, tool, args):
        res = self.browser.execute_browser_tool(None, tool, args)
        return "Not opening the machine browser" in str(res.get("error", ""))

    def test_browser_open_url_is_guarded(self):
        turn.bind_message("play despacito on youtube")
        self.assertTrue(self._refused("browser_open_url",
                                      {"url": "https://youtu.be/kJQP7ciFHFo"}))

    def test_system_open_is_guarded_too(self):
        # xdg-open reaches the machine's browser by another door.
        turn.bind_message("play this song")
        self.assertTrue(self._refused("system_open",
                                      {"target": "https://youtu.be/kJQP7ciFHFo"}))

    def test_play_request_cannot_reach_the_browser(self):
        turn.bind_message("Travis play unfold for Porter Robinson on YouTube")
        self.assertTrue(self._refused("browser_open_url", {
            "url": "https://www.youtube.com/results?search_query=Porter+Robinson+Unfold"}))

    def test_automation_is_untouched(self):
        turn.bind_message("open novatek and log in")
        self.assertFalse(self._refused("browser_open_url",
                                       {"url": "https://nec-dev.autotrial.app"}))


if __name__ == "__main__":
    unittest.main(verbosity=2)
