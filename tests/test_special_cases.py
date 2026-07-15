"""Tests for framework-specific fast extractors."""

from __future__ import annotations

import json

import pytest

from docpull.conversion.special_cases import (
    DEFAULT_CHAIN,
    DocusaurusExtractor,
    GitBookExtractor,
    MintlifyExtractor,
    MkDocsMaterialExtractor,
    NextDataExtractor,
    OpenApiExtractor,
    RawTextExtractor,
    ReadMeExtractor,
    RedocScalarExtractor,
    RfcEditorExtractor,
    SpecialCaseResult,
    StarlightExtractor,
    VitePressExtractor,
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

    def test_prefers_raw_mdx_over_compiled_source(self):
        payload = {
            "props": {
                "pageProps": {
                    "title": "Doc",
                    "mdxSource": {
                        "compiledSource": 'function MDXContent(){return "compiled";}' * 8,
                        "raw": "# Raw Doc\n\nReadable markdown body. " * 8,
                    },
                }
            }
        }
        html = (
            b'<html><body><script id="__NEXT_DATA__">'
            + json.dumps(payload).encode()
            + b"</script></body></html>"
        )

        result = NextDataExtractor().try_extract(html, "https://example.com/")

        assert result is not None
        assert "Readable markdown body" in result.markdown
        assert "function MDXContent" not in result.markdown


class TestStaticFrameworkExtractors:
    @pytest.mark.parametrize(
        ("extractor", "head_marker", "selector_open", "selector_close", "source_type", "framework"),
        [
            (
                MkDocsMaterialExtractor(),
                '<meta name="generator" content="mkdocs">',
                '<article class="md-content__inner">',
                "</article>",
                "mkdocs",
                "mkdocs",
            ),
            (
                VitePressExtractor(),
                '<meta name="generator" content="VitePress">',
                '<div class="VPDoc"><div class="content vp-doc">',
                "</div></div>",
                "vitepress",
                "vitepress",
            ),
            (
                StarlightExtractor(),
                '<meta name="generator" content="astro starlight">',
                '<main class="sl-markdown-content">',
                "</main>",
                "starlight",
                "starlight",
            ),
            (
                GitBookExtractor(),
                '<meta name="generator" content="gitbook">',
                '<main data-testid="page.content">',
                "</main>",
                "gitbook",
                "gitbook",
            ),
            (
                ReadMeExtractor(),
                '<meta property="og:site_name" content="readme.io">',
                '<article class="rm-markdown">',
                "</article>",
                "readme",
                "readme",
            ),
            (
                RedocScalarExtractor(),
                '<meta name="generator" content="redoc">',
                '<main class="api-content">',
                "</main>",
                "api_reference",
                "redoc_scalar",
            ),
        ],
    )
    def test_common_static_framework_extractors(
        self,
        extractor,
        head_marker: str,
        selector_open: str,
        selector_close: str,
        source_type: str,
        framework: str,
    ):
        html = (
            "<!doctype html><html><head><title>Framework Guide</title>"
            f"{head_marker}</head><body>{selector_open}"
            "<h1>Framework Guide</h1>"
            "<p>Rendered documentation content is available in static HTML.</p>"
            + ("<p>Reference paragraph for local context extraction.</p>" * 4)
            + f"{selector_close}</body></html>"
        ).encode()

        result = extractor.try_extract(html, "https://docs.example.com/framework")

        assert result is not None
        assert result.source_type == source_type
        assert result.extra == {"framework": framework}
        assert result.title == "Framework Guide"
        assert "# Framework Guide" in result.markdown

    def test_docusaurus_extracts_static_article_and_tags_framework(self):
        html = (
            b"<!doctype html><html><head>"
            b'<meta name="generator" content="Docusaurus v3">'
            b"<title>Docusaurus Intro</title></head><body>"
            b'<div id="__docusaurus"><main><article>'
            b"<h1>Intro</h1><p>Docusaurus content renders statically for extraction.</p>"
            + (b"<p>Body paragraph for documentation users.</p>" * 4)
            + b"</article></main></div></body></html>"
        )

        result = DocusaurusExtractor().try_extract(html, "https://docs.example.com/intro")

        assert result is not None
        assert result.source_type == "docusaurus"
        assert result.extra == {"framework": "docusaurus"}
        assert result.title == "Docusaurus Intro"
        assert "# Intro" in result.markdown

    def test_sphinx_extracts_static_body_and_tags_framework(self):
        html = (
            b"<!doctype html><html><head>"
            b'<meta name="generator" content="Sphinx 8.0">'
            b"<title>Sphinx API</title></head><body>"
            b'<div class="document"><div class="body" role="main">'
            b"<h1>Sphinx API</h1><p>Sphinx content is server-rendered HTML.</p>"
            + (b"<p>Reference paragraph for generated docs.</p>" * 4)
            + b"</div></div></body></html>"
        )

        result = DEFAULT_CHAIN[-1].try_extract(html, "https://pkg.readthedocs.io/en/latest/api.html")

        assert result is not None
        assert result.source_type == "sphinx"
        assert result.extra == {"framework": "sphinx"}
        assert result.title == "Sphinx API"
        assert "# Sphinx API" in result.markdown


class TestRfcEditorExtractor:
    def test_extracts_the_complete_rfc_body_instead_of_one_section(self) -> None:
        html = (
            b"<!doctype html><html><head><title>RFC 9999</title></head><body>"
            b"<header>Site chrome</header><main>"
            b'<section id="S:1"><h1>RFC 9999</h1><p>Introduction evidence. '
            b"Complete standards content for interoperable implementations.</p></section>"
            b'<section id="S:2"><h2>Security Considerations</h2>'
            b"<p>Second section evidence with requirements and terminology.</p></section>"
            b"</main><footer>Footer chrome</footer></body></html>"
        )

        result = RfcEditorExtractor().try_extract(
            html,
            "https://www.rfc-editor.org/rfc/rfc9999.html",
        )

        assert result is not None
        assert result.source_type == "rfc_editor"
        assert "Introduction evidence" in result.markdown
        assert "Security Considerations" in result.markdown
        assert "Site chrome" not in result.markdown


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
                                    "application/json": {"schema": {"$ref": "#/components/schemas/Thing"}}
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
                        "description": 'Plain <a href="/foo">link</a> in <b>bold</b>',
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
                            {
                                "name": "expand",
                                "in": "query",
                                "schema": {"type": "array", "items": {"type": "string"}},
                            },
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
                            "content": {"application/json": {"schema": {"$ref": "#/components/schemas/A"}}}
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

    def test_allof_type_hint_renders_as_intersection(self):
        spec = {
            "openapi": "3.1.0",
            "info": {"title": "API"},
            "paths": {
                "/thing": {
                    "get": {
                        "responses": {
                            "200": {
                                "description": "ok",
                                "content": {
                                    "application/json": {
                                        "schema": {
                                            "allOf": [
                                                {"$ref": "#/components/schemas/A"},
                                                {"type": "object"},
                                            ]
                                        }
                                    }
                                },
                            }
                        }
                    }
                }
            },
            "components": {"schemas": {"A": {"type": "object"}}},
        }

        result = OpenApiExtractor().try_extract(json.dumps(spec).encode(), "x")

        assert result is not None
        assert "`200` → `(A & object)`" in result.markdown
        assert "`200` → `(A | object)`" not in result.markdown


class TestRawTextExtractor:
    def test_extracts_llms_txt_as_markdown(self):
        body = (
            "# Parallel\n\n"
            "- [Search](https://docs.parallel.ai/search/search.md): Searches the web.\n"
            "- [Extract](https://docs.parallel.ai/extract/extract.md): Extracts URLs.\n"
        )

        result = RawTextExtractor().try_extract(
            body.encode(),
            "https://docs.parallel.ai/llms.txt",
        )

        assert result is not None
        assert result.source_type == "llms_txt"
        assert result.title == "Parallel"
        assert "Search" in result.markdown

    def test_rejects_non_docs_txt(self):
        assert RawTextExtractor().try_extract(b"hello", "https://example.com/file.txt") is None

    def test_accepts_plain_prose_when_response_identifies_text(self):
        body = b"Request for Comments\n\nInteroperability Considerations\n\nParsers must accept JSON.\n"

        result = RawTextExtractor().try_extract(
            body,
            "https://www.rfc-editor.org/rfc/rfc8259.txt",
            content_type="text/plain; charset=utf-8",
        )

        assert result is not None
        assert result.source_type == "raw_text"
        assert "Interoperability Considerations" in result.markdown

    def test_accepts_rst_yaml_and_extensionless_typed_text(self):
        cases = [
            ("https://example.com/README.rst", b"Project\n=======\n\nBuild Instructions\n", None),
            ("https://example.com/spec.yaml", b"openapi: 3.0.0\ninfo:\n  title: Example\n", None),
            ("https://example.com/source", b"plain response body", "text/plain"),
        ]

        for url, body, content_type in cases:
            result = RawTextExtractor().try_extract(body, url, content_type=content_type)
            assert result is not None

    def test_rejects_html_mislabeled_as_text(self):
        result = RawTextExtractor().try_extract(
            b"<!doctype html><html><body>challenge</body></html>",
            "https://example.com/source",
            content_type="text/plain",
        )

        assert result is None

    def test_frontmatter_title_is_used_for_raw_markdown(self):
        body = "---\ntitle: Original\n---\n\n# Body\n\nContent.\n"

        result = RawTextExtractor().try_extract(body.encode(), "https://example.com/page.md")

        assert result is not None
        assert result.title == "Original"
        assert result.markdown.startswith("---\n")

    def test_frontmatter_title_handles_bom_and_crlf(self):
        body = "\ufeff---\r\ntitle: Windows Markdown\r\n---\r\n\r\n# Body\r\n"

        result = RawTextExtractor().try_extract(body.encode(), "https://example.com/page.md")

        assert result is not None
        assert result.title == "Windows Markdown"


class TestMintlifyExtractor:
    def test_matches_when_marker_present_and_next_data_parses(self):
        payload = {"props": {"pageProps": {"title": "Doc", "source": "content " * 40}}}
        html = (
            b"<html><head><meta name=generator content=Mintlify></head><body>"
            b'<script id="__NEXT_DATA__">' + json.dumps(payload).encode() + b"</script></body></html>"
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

    def test_deceptive_readthedocs_suffix_is_generic(self):
        assert detect_source_type(b"<html></html>", "https://readthedocs.io.evil.example/page") == "generic"

    def test_readme_host_assumes_readme(self):
        assert detect_source_type(b"<html></html>", "https://docs.readme.io/page") == "readme"

    def test_deceptive_readme_suffix_is_generic(self):
        assert detect_source_type(b"<html></html>", "https://readme.io.evil.example/page") == "generic"


class TestDefaultChain:
    def test_chain_has_expected_extractors(self):
        names = {e.name for e in DEFAULT_CHAIN}
        assert {"openapi", "raw_text", "next_data", "mintlify", "docusaurus", "sphinx"} <= names
