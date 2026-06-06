"""Tests for rich metadata extraction."""

from docpull.metadata_extractor import RichMetadataExtractor


def test_extract_opengraph_handles_extruct_tuple_properties() -> None:
    extractor = RichMetadataExtractor()

    result = extractor._extract_opengraph(
        [
            ("og:title", "Open Graph Title"),
            ("og:description", "Open Graph description"),
            ("og:image", "https://example.com/og.png"),
            ("article:tag", "python"),
            ("article:tag", "docs"),
        ]
    )

    assert result["title"] == "Open Graph Title"
    assert result["description"] == "Open Graph description"
    assert result["image"] == "https://example.com/og.png"
    assert result["tags"] == ["python", "docs"]


def test_extract_prefers_opengraph_over_jsonld_when_both_exist() -> None:
    extractor = RichMetadataExtractor()
    html = """
    <html>
      <head>
        <meta property="og:title" content="Open Graph Title">
        <meta property="og:description" content="Open Graph description">
        <script type="application/ld+json">
        {
          "@context": "https://schema.org",
          "@type": "Article",
          "headline": "JSON-LD Title",
          "description": "JSON-LD description",
          "author": {"name": "Ada Lovelace"},
          "keywords": "python, docs",
          "image": [{"url": "https://example.com/hero.png"}]
        }
        </script>
      </head>
      <body></body>
    </html>
    """

    result = extractor.extract(html, "https://example.com/docs")

    assert result["title"] == "Open Graph Title"
    assert result["description"] == "Open Graph description"
    assert result["author"] == "Ada Lovelace"
    assert result["keywords"] == ["python", "docs"]
    assert result["image"] == "https://example.com/hero.png"


def test_merge_with_fallback_removes_empty_values() -> None:
    extractor = RichMetadataExtractor()

    result = extractor.merge_with_fallback(
        {"url": "https://example.com/docs", "title": None, "description": ""},
        fallback_title="Fallback",
    )

    assert result == {"url": "https://example.com/docs", "title": "Fallback"}
