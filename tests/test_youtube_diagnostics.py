import unittest

from cogs.music import _cookie_validation, _is_youtube_url


class YoutubeDiagnosticTests(unittest.TestCase):
    def test_accepts_netscape_cookie_structure_without_exposing_values(self):
        data = (
            b"# Netscape HTTP Cookie File\n"
            b"#HttpOnly_.youtube.com\tTRUE\t/\tTRUE\t0\tname\tplaceholder\n"
        )
        valid, reason, count = _cookie_validation(data)
        self.assertTrue(valid)
        self.assertEqual(reason, "valid_netscape")
        self.assertEqual(count, 1)

    def test_rejects_non_netscape_data(self):
        valid, reason, _ = _cookie_validation(b"not a cookie export")
        self.assertFalse(valid)
        self.assertEqual(reason, "invalid_columns")

    def test_only_accepts_youtube_hosts_for_remote_diagnostic(self):
        self.assertTrue(_is_youtube_url("https://www.youtube.com/watch?v=msa8KUwXbz0"))
        self.assertTrue(_is_youtube_url("https://youtu.be/msa8KUwXbz0"))
        self.assertFalse(_is_youtube_url("https://example.com/watch?v=msa8KUwXbz0"))


if __name__ == "__main__":
    unittest.main()
