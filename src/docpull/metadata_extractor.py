"""Rich metadata extraction from HTML using structured data."""

from __future__ import annotations

import logging
from typing import Any, TypedDict

logger = logging.getLogger(__name__)


class RichMetadata(TypedDict, total=False):
    """Type for rich structured metadata extracted from HTML."""

    # Basic fields (always present)
    url: str
    title: str | None

    # Open Graph fields
    description: str | None
    image: str | None
    type: str | None
    site_name: str | None

    # Article-specific
    author: str | None
    published_time: str | None
    modified_time: str | None
    section: str | None
    tags: list[str] | None

    # SEO/Meta fields
    keywords: list[str] | None
    canonical_url: str | None


class RichMetadataExtractor:
    """Extract structured metadata from HTML pages using extruct."""

    def __init__(self, base_url: str = "") -> None:
        """Initialize the extractor.

        Args:
            base_url: Base URL for resolving relative URLs
        """
        self.base_url = base_url

    def extract(self, html: str, url: str) -> RichMetadata:
        """Extract rich structured metadata from HTML.

        Args:
            html: HTML content
            url: Page URL

        Returns:
            Rich metadata dictionary
        """
        metadata: RichMetadata = {"url": url, "title": None}

        try:
            import extruct

            # Extract all structured data
            data = extruct.extract(
                html,
                base_url=url,
                syntaxes=["opengraph", "json-ld", "microdata"],
                errors="ignore",
            )

            # Extract Open Graph data
            og_data = data.get("opengraph", [])
            if og_data and isinstance(og_data, list) and len(og_data) > 0:
                og = og_data[0].get("properties", [])
                if og:
                    metadata.update(self._extract_opengraph(og))  # type: ignore[typeddict-item]

            # Extract JSON-LD data
            jsonld_data = data.get("json-ld", [])
            if jsonld_data and isinstance(jsonld_data, list):
                self._merge_missing(metadata, self._extract_jsonld(jsonld_data))

            # Extract microdata
            microdata = data.get("microdata", [])
            if microdata and isinstance(microdata, list):
                self._merge_missing(metadata, self._extract_microdata(microdata))

        except ImportError:
            logger.warning("extruct not installed, rich metadata extraction disabled")
        except Exception as e:
            logger.debug(f"Could not extract rich metadata from {url}: {e}")

        return metadata

    def _merge_missing(self, target: RichMetadata, source: dict[str, Any]) -> None:
        """Fill metadata fields without overwriting higher-priority sources."""
        for key, value in source.items():
            if value and not target.get(key):  # type: ignore[literal-required]
                target[key] = value  # type: ignore[literal-required]

    def _extract_opengraph(self, og_properties: list[Any]) -> dict[str, Any]:
        """Extract Open Graph metadata.

        Args:
            og_properties: Open Graph properties list

        Returns:
            Dictionary of extracted OG data
        """
        result: dict[str, Any] = {}

        # Build dict from extruct's property list. Current extruct returns
        # tuples like ("og:title", "Title"), while older/faked tests may use
        # dicts. Preserve repeated values such as article:tag.
        og_dict: dict[str, Any] = {}

        def add_value(key: str, value: Any) -> None:
            clean_key = key.replace("og:", "")
            existing = og_dict.get(clean_key)
            if existing is None:
                og_dict[clean_key] = value
            elif isinstance(existing, list):
                existing.append(value)
            else:
                og_dict[clean_key] = [existing, value]

        for prop in og_properties:
            if isinstance(prop, dict):
                for key, value in prop.items():
                    add_value(key, value)
            elif isinstance(prop, (list, tuple)) and len(prop) == 2:
                key, value = prop
                if isinstance(key, str):
                    add_value(key, value)

        # Map OG fields to our metadata
        if "title" in og_dict:
            result["title"] = self._safe_string(og_dict["title"])

        if "description" in og_dict:
            result["description"] = self._safe_string(og_dict["description"])

        if "image" in og_dict:
            result["image"] = self._safe_string(og_dict["image"])

        if "type" in og_dict:
            result["type"] = self._safe_string(og_dict["type"])

        if "site_name" in og_dict:
            result["site_name"] = self._safe_string(og_dict["site_name"])

        if "url" in og_dict:
            result["canonical_url"] = self._safe_string(og_dict["url"])

        # Article-specific fields
        if "article:author" in og_dict:
            result["author"] = self._safe_string(og_dict["article:author"])

        if "article:published_time" in og_dict:
            result["published_time"] = self._safe_string(og_dict["article:published_time"])

        if "article:modified_time" in og_dict:
            result["modified_time"] = self._safe_string(og_dict["article:modified_time"])

        if "article:section" in og_dict:
            result["section"] = self._safe_string(og_dict["article:section"])

        if "article:tag" in og_dict:
            tags = og_dict["article:tag"]
            if isinstance(tags, list):
                result["tags"] = [self._safe_string(t) for t in tags if t]
            elif isinstance(tags, str):
                result["tags"] = [self._safe_string(tags)]

        return result

    def _extract_jsonld(self, jsonld_list: list[dict[str, Any]]) -> dict[str, Any]:
        """Extract JSON-LD metadata.

        Args:
            jsonld_list: List of JSON-LD objects

        Returns:
            Dictionary of extracted JSON-LD data
        """
        result: dict[str, Any] = {}

        for item in jsonld_list:
            if not isinstance(item, dict):
                continue

            # Extract common fields
            if "headline" in item and not result.get("title"):
                result["title"] = self._safe_string(item["headline"])

            if "description" in item and not result.get("description"):
                result["description"] = self._safe_string(item["description"])

            if "author" in item and not result.get("author"):
                author = item["author"]
                if isinstance(author, dict):
                    result["author"] = self._safe_string(author.get("name", ""))
                elif isinstance(author, str):
                    result["author"] = self._safe_string(author)

            if "datePublished" in item and not result.get("published_time"):
                result["published_time"] = self._safe_string(item["datePublished"])

            if "dateModified" in item and not result.get("modified_time"):
                result["modified_time"] = self._safe_string(item["dateModified"])

            if "keywords" in item and not result.get("keywords"):
                keywords = item["keywords"]
                if isinstance(keywords, str):
                    # Split comma-separated keywords
                    result["keywords"] = [k.strip() for k in keywords.split(",") if k.strip()]
                elif isinstance(keywords, list):
                    result["keywords"] = [self._safe_string(k) for k in keywords if k]

            if "image" in item and not result.get("image"):
                image = item["image"]
                if isinstance(image, dict):
                    result["image"] = self._safe_string(image.get("url", ""))
                elif isinstance(image, list):
                    result["image"] = self._extract_image_from_list(image)
                elif isinstance(image, str):
                    result["image"] = self._safe_string(image)

        return result

    def _extract_image_from_list(self, images: list[Any]) -> str:
        """Extract the first useful image URL from JSON-LD image arrays."""
        for image in images:
            if isinstance(image, dict):
                value = self._safe_string(image.get("url", ""))
            else:
                value = self._safe_string(image)
            if value:
                return value
        return ""

    def _extract_microdata(self, microdata_list: list[dict[str, Any]]) -> dict[str, Any]:
        """Extract microdata metadata.

        Args:
            microdata_list: List of microdata objects

        Returns:
            Dictionary of extracted microdata
        """
        result: dict[str, Any] = {}

        for item in microdata_list:
            if not isinstance(item, dict):
                continue

            properties = item.get("properties", {})
            if not properties:
                continue

            # Extract relevant fields
            if "headline" in properties and not result.get("title"):
                result["title"] = self._safe_string(properties["headline"])

            if "description" in properties and not result.get("description"):
                result["description"] = self._safe_string(properties["description"])

            if "author" in properties and not result.get("author"):
                author = properties["author"]
                if isinstance(author, dict):
                    result["author"] = self._safe_string(author.get("properties", {}).get("name", ""))
                elif isinstance(author, str):
                    result["author"] = self._safe_string(author)

        return result

    def _safe_string(self, value: Any) -> str:
        """Safely convert value to string.

        Args:
            value: Value to convert

        Returns:
            String value or empty string
        """
        if value is None:
            return ""
        if isinstance(value, str):
            return value.strip()
        if isinstance(value, (list, tuple)):
            if len(value) > 0:
                return str(value[0]).strip()
            return ""
        return str(value).strip()

    def merge_with_fallback(
        self, rich_metadata: RichMetadata, fallback_title: str | None = None
    ) -> dict[str, Any]:
        """Merge rich metadata with fallback values.

        Args:
            rich_metadata: Rich metadata from extruct
            fallback_title: Fallback title if none found

        Returns:
            Merged metadata dictionary
        """
        result: dict[str, Any] = dict(rich_metadata)

        # Use fallback title if no title found
        if not result.get("title") and fallback_title:
            result["title"] = fallback_title

        # Clean up empty values
        result = {k: v for k, v in result.items() if v}

        return result
