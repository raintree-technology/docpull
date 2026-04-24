"""Regression tests for bugs found during real-site validation."""

from __future__ import annotations

from unittest.mock import MagicMock

from docpull.conversion.markdown import HtmlToMarkdown
from docpull.conversion.special_cases import detect_source_type
from docpull.pipeline.steps.fetch import ALLOWED_CONTENT_TYPES
from docpull.security.robots import RobotsChecker, _CaseInsensitiveHeaders, _RobotsResponse


class TestCaseInsensitiveHeaders:
    def test_lowercase_key_lookup_finds_mixed_case_value(self):
        h = _CaseInsensitiveHeaders([("Location", "/next")])
        assert h.get("location") == "/next"
        assert h.get("Location") == "/next"

    def test_lowercase_value_stored_wins(self):
        # Cloudflare sends lowercase HTTP/2 headers.
        h = _CaseInsensitiveHeaders([("location", "/next")])
        assert h.get("Location") == "/next"


class TestNextjsAppRouterDetection:
    def test_detects_rsc_streaming_flush(self):
        html = b"<html><head></head><body>...<script>self.__next_f.push([1,'x'])</script></body></html>"
        assert detect_source_type(html, "https://nextjs.org/docs/page") == "nextjs"

    def test_detects_static_path(self):
        html = b'<html><head><link href="/_next/static/chunks/app.js"></head><body></body></html>'
        assert detect_source_type(html, "https://example.com/") == "nextjs"

    def test_detects_router_state_tree(self):
        html = b'<html><body><meta name="next-router-state-tree" content=""></body></html>'
        assert detect_source_type(html, "https://example.com/") == "nextjs"


class TestHtml2TextLinkUnmangling:
    def test_strips_angle_bracketed_inner_url(self):
        c = HtmlToMarkdown()
        dirty = "[Docs](https://example.com/prefix/<https:/example.com/real>)"
        cleaned = c._clean_output(dirty)
        assert cleaned.strip() == "[Docs](https://example.com/real)"

    def test_handles_empty_link_text(self):
        c = HtmlToMarkdown()
        dirty = "[](https://example.com/prefix/<https:/example.com/real>)"
        cleaned = c._clean_output(dirty)
        assert cleaned.strip() == "[](https://example.com/real)"

    def test_preserves_well_formed_links(self):
        c = HtmlToMarkdown()
        md = "[Docs](https://example.com/page)"
        cleaned = c._clean_output(md)
        assert cleaned.strip() == md


class TestRobots4xxHandling:
    """RFC 9309 §2.3.1.3: 4xx means 'allow all', only 5xx blocks."""

    def _make_checker(self, status_code: int) -> RobotsChecker:
        checker = RobotsChecker()
        response = _RobotsResponse(
            status_code=status_code,
            headers=_CaseInsensitiveHeaders(),
            text="",
        )
        checker._fetch_url = MagicMock(return_value=response)  # type: ignore[method-assign]
        checker._validate_url = MagicMock(return_value=True)  # type: ignore[method-assign]
        return checker

    def test_400_bad_request_treated_as_allow(self):
        """raw.githubusercontent.com returns 400 on robots.txt."""
        checker = self._make_checker(400)
        assert checker.is_allowed("https://raw.githubusercontent.com/foo/bar") is True

    def test_404_missing_treated_as_allow(self):
        checker = self._make_checker(404)
        assert checker.is_allowed("https://example.com/") is True

    def test_401_unauthorized_treated_as_allow(self):
        checker = self._make_checker(401)
        assert checker.is_allowed("https://example.com/") is True

    def test_500_server_error_blocks(self):
        checker = self._make_checker(500)
        # 5xx: we don't know the site's policy, be conservative
        assert checker.is_allowed("https://example.com/") is False


class TestContentTypeAllowlist:
    """JSON and Markdown should be fetchable so special-case extractors work."""

    def test_json_allowed(self):
        assert "application/json" in ALLOWED_CONTENT_TYPES

    def test_markdown_allowed(self):
        assert "text/markdown" in ALLOWED_CONTENT_TYPES

    def test_plain_text_allowed(self):
        assert "text/plain" in ALLOWED_CONTENT_TYPES


class TestSpaOutputDetection:
    def test_loading_shell_flagged(self):
        from docpull.conversion.special_cases import looks_like_spa_output

        assert looks_like_spa_output("Loading...\n\nLoading...\n\nLoading...") is True

    def test_real_content_not_flagged(self):
        from docpull.conversion.special_cases import looks_like_spa_output

        md = "# Real Title\n\n" + ("This is real documentation content. " * 30)
        assert looks_like_spa_output(md) is False

    def test_empty_flagged(self):
        from docpull.conversion.special_cases import looks_like_spa_output

        assert looks_like_spa_output("") is True
        assert looks_like_spa_output("   \n\n  ") is True
