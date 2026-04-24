"""Tests for framework-specific fast extractors."""

from __future__ import annotations

import json

import pytest

from docpull.conversion.special_cases import (
    DEFAULT_CHAIN,
    DocusaurusExtractor,
    MintlifyExtractor,
    NextDataExtractor,
    OpenApiExtractor,
    SpecialCaseResult,
    detect_source_type,
    looks_like_spa,
)


class TestNextDataExtractor:
    def test_extracts_string_source(self):
        payload = {
            "props": {
                "pageProps": {
                    "title": "Getting Started",
                    "source": "# Getting Started\n\n" + ("Some content paragraph. " * 20),
                }
            }
        }
        html = (
            b'<html><body><script id="__NEXT_DATA__">'
            + json.dumps(payload).encode()
            + b"</script></body></html>"
        )
        result = NextDataExtractor().try_extract(html, "https://example.com/")
        assert isinstance(result, SpecialCaseResult)
        assert result.source_type == "next_data"
        assert result.title == "Getting Started"
        assert "Getting Started" in result.markdown
        assert "Some content paragraph" in result.markdown

    def test_returns_none_when_marker_absent(self):
        html = b"<html><body>no next data here</body></html>"
        assert NextDataExtractor().try_extract(html, "https://example.com/") is None

    def test_returns_none_on_malformed_json(self):
        html = b'<html><body><script id="__NEXT_DATA__">{not json}</script></body></html>'
        assert NextDataExtractor().try_extract(html, "https://example.com/") is None

    def test_returns_none_on_empty_body(self):
        html = b'<html><body><script id="__NEXT_DATA__">{"props":{"pageProps":{}}}</script></body></html>'
        assert NextDataExtractor().try_extract(html, "https://example.com/") is None


class TestOpenApiExtractor:
    def test_renders_openapi_to_markdown(self):
        spec = {
            "openapi": "3.0.0",
            "info": {"title": "Widget API", "description": "Manage widgets."},
            "paths": {
                "/widgets": {
                    "get": {
                        "summary": "List widgets",
                        "description": "Returns all widgets.",
                        "parameters": [
                            {"name": "limit", "in": "query", "description": "Max rows"},
                        ],
                    }
                }
            },
        }
        html = json.dumps(spec).encode()
        result = OpenApiExtractor().try_extract(html, "https://example.com/openapi.json")
        assert result is not None
        assert result.source_type == "openapi"
        assert result.title == "Widget API"
        assert "# Widget API" in result.markdown
        assert "/widgets" in result.markdown
        assert "List widgets" in result.markdown
        assert "`limit`" in result.markdown

    def test_rejects_non_json(self):
        assert OpenApiExtractor().try_extract(b"<html></html>", "https://example.com/") is None

    def test_rejects_json_without_openapi_key(self):
        html = json.dumps({"foo": "bar"}).encode()
        assert OpenApiExtractor().try_extract(html, "https://example.com/") is None

    def test_renders_request_body_properties_and_resolves_refs(self):
        spec = {
            "openapi": "3.0.0",
            "info": {"title": "API"},
            "paths": {
                "/v1/things": {
                    "post": {
                        "summary": "Create",
                        "requestBody": {
                            "required": True,
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/ThingInput"},
                                }
                            },
                        },
                        "responses": {
                            "200": {
                                "description": "OK",
                                "content": {
                                    "application/json": {
                                        "schema": {"$ref": "#/components/schemas/Thing"}
                                    }
                                },
                            }
                        },
                    }
                }
            },
            "components": {
                "schemas": {
                    "ThingInput": {
                        "type": "object",
                        "required": ["name"],
                        "properties": {
                            "name": {"type": "string", "description": "Name"},
                            "count": {"type": "integer", "format": "int32"},
                        },
                    },
                    "Thing": {"type": "object", "properties": {"id": {"type": "string"}}},
                }
            },
        }
        html = json.dumps(spec).encode()
        result = OpenApiExtractor().try_extract(html, "https://example.com/openapi.json")
        assert result is not None
        md = result.markdown
        assert "Request body" in md
        assert "`application/json`" in md
        assert "`name` (string) (required)" in md
        assert "`count` (integer(int32))" in md
        assert "**Responses:**" in md
        assert "`200` → `Thing`" in md

    def test_strips_html_tags_from_descriptions(self):
        spec = {
            "openapi": "3.0.0",
            "info": {"title": "API", "description": "<p>Top <code>level</code></p>"},
            "paths": {
                "/v1/x": {
                    "get": {
                        "description": "Plain <a href=\"/foo\">link</a> in <b>bold</b>",
                        "responses": {"200": {"description": "ok"}},
                    }
                }
            },
        }
        html = json.dumps(spec).encode()
        result = OpenApiExtractor().try_extract(html, "https://example.com/openapi.json")
        assert result is not None
        md = result.markdown
        assert "<p>" not in md
        assert "<code>" not in md
        assert "<a " not in md
        assert "Top level" in md
        assert "Plain link in bold" in md

    def test_separates_path_and_query_parameters(self):
        spec = {
            "openapi": "3.0.0",
            "info": {"title": "API"},
            "paths": {
                "/items/{id}": {
                    "get": {
                        "parameters": [
                            {"name": "id", "in": "path", "required": True, "schema": {"type": "string"}},
                            {"name": "expand", "in": "query", "schema": {"type": "array", "items": {"type": "string"}}},
                        ],
                        "responses": {"200": {"description": "ok"}},
                    }
                }
            },
        }
        html = json.dumps(spec).encode()
        result = OpenApiExtractor().try_extract(html, "https://example.com/openapi.json")
        assert result is not None
        md = result.markdown
        assert "**Path parameters:**" in md
        assert "**Query parameters:**" in md
        assert "`id` (string) (required)" in md
        assert "`expand` (array<string>)" in md

    def test_handles_form_encoded_request_body(self):
        spec = {
            "openapi": "3.0.0",
            "info": {"title": "API"},
            "paths": {
                "/v1/x": {
                    "post": {
                        "requestBody": {
                            "content": {
                                "application/x-www-form-urlencoded": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {"a": {"type": "string"}},
                                    }
                                }
                            }
                        },
                        "responses": {"200": {"description": "ok"}},
                    }
                }
            },
        }
        result = OpenApiExtractor().try_extract(json.dumps(spec).encode(), "x")
        assert result is not None
        assert "application/x-www-form-urlencoded" in result.markdown
        assert "`a` (string)" in result.markdown

    def test_circular_refs_do_not_recurse_forever(self):
        spec = {
            "openapi": "3.0.0",
            "info": {"title": "API"},
            "paths": {
                "/x": {
                    "post": {
                        "requestBody": {
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/A"}
                                }
                            }
                        },
                        "responses": {"200": {"description": "ok"}},
                    }
                }
            },
            "components": {
                "schemas": {
                    "A": {"$ref": "#/components/schemas/B"},
                    "B": {"$ref": "#/components/schemas/A"},
                }
            },
        }
        result = OpenApiExtractor().try_extract(json.dumps(spec).encode(), "x")
        assert result is not None


class TestMintlifyExtractor:
    def test_matches_when_marker_present_and_next_data_parses(self):
        payload = {
            "props": {
                "pageProps": {"title": "Doc", "source": "content " * 40}
            }
        }
        html = (
            b"<html><head><meta name=generator content=Mintlify></head><body>"
            b'<script id="__NEXT_DATA__">'
            + json.dumps(payload).encode()
            + b"</script></body></html>"
        )
        result = MintlifyExtractor().try_extract(html, "https://example.com/")
        assert result is not None
        assert result.source_type == "mintlify"


class TestDocusaurusExtractor:
    def test_always_delegates_to_generic(self):
        html = b"<html><body><div id=__docusaurus></div></body></html>"
        assert DocusaurusExtractor().try_extract(html, "https://example.com/") is None


class TestSpaDetection:
    def test_detects_empty_spa(self):
        html = b'<html><body><div id="root"></div><script>' + b"x" * 5000 + b"</script></body></html>"
        assert looks_like_spa(html) is True

    def test_not_spa_when_content_present(self):
        html = b"<html><body>" + b"Real content with words. " * 200 + b"</body></html>"
        assert looks_like_spa(html) is False

    def test_not_spa_without_scripts(self):
        html = b"<html><body><div></div></body></html>"
        assert looks_like_spa(html) is False


class TestDetectSourceType:
    @pytest.mark.parametrize(
        "html, expected",
        [
            (b'<script id="__NEXT_DATA__">{}</script>', "nextjs"),
            (b"<meta name=generator content=Mintlify>", "mintlify"),
            (b"<div>docusaurus</div>", "docusaurus"),
            (b'<meta name="generator" content="Sphinx 4.0"/>', "sphinx"),
            (b"<html></html>", "generic"),
        ],
    )
    def test_detection(self, html, expected):
        assert detect_source_type(html, "https://example.com/") == expected

    def test_readthedocs_host_assumes_sphinx(self):
        assert detect_source_type(b"<html></html>", "https://foo.readthedocs.io/page") == "sphinx"


class TestDefaultChain:
    def test_chain_has_expected_extractors(self):
        names = {e.name for e in DEFAULT_CHAIN}
        assert {"openapi", "next_data", "mintlify", "docusaurus", "sphinx"} <= names
