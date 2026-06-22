"""Provider adapter tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from docpull.provider_adapters import ExaAdapter, ProviderAdapterError, TavilyAdapter


def test_tavily_adapter_map_pack_writes_discovery_pack(tmp_path: Path) -> None:
    def fake_post(**kwargs: object) -> dict[str, object]:
        body = kwargs["body"]
        assert isinstance(body, dict)
        assert kwargs["url"] == "https://api.tavily.com/map"
        assert kwargs["headers"] == {"Authorization": "Bearer test-tavily-key"}
        assert body["url"] == "https://docs.example.com"
        assert body["include_usage"] is True
        return {
            "base_url": "https://docs.example.com",
            "results": [
                "https://docs.example.com/api",
                "https://blog.example.com/post",
            ],
            "usage": {"credits": 1},
            "request_id": "map-request-1",
            "response_time": 0.2,
        }

    adapter = TavilyAdapter(api_key="test-tavily-key", http_post=fake_post)
    report = adapter.map_pack(
        url="https://docs.example.com",
        output_dir=tmp_path / "map",
        objective="Map API docs",
        query="API docs",
        instructions=None,
        include_domains=["docs.example.com"],
        exclude_domains=[],
        select_paths=[],
        select_domains=[],
        exclude_paths=[],
        map_exclude_domains=[],
        max_depth=1,
        max_breadth=20,
        limit=10,
        allow_external=False,
        timeout=30.0,
    )

    assert report["provider"] == "tavily"
    assert report["candidate_count"] == 1
    assert report["skipped_count"] == 1
    candidate_text = (tmp_path / "map" / "candidate_sources.ndjson").read_text(encoding="utf-8")
    assert "https://docs.example.com/api" in candidate_text
    assert "test-tavily-key" not in candidate_text


def test_tavily_adapter_uses_bearer_auth_without_persisting_key(tmp_path: Path) -> None:
    def fake_post(**kwargs: object) -> dict[str, object]:
        assert kwargs["headers"] == {"Authorization": "Bearer test-tavily-key"}
        if kwargs["label"] == "Tavily Search":
            return {
                "results": [
                    {
                        "url": "https://docs.example.com/a",
                        "title": "A",
                        "content": "Fallback content",
                    }
                ],
                "usage": {"credits": 1},
                "request_id": "search-request",
            }
        assert kwargs["label"] == "Tavily Extract"
        return {
            "results": [
                {
                    "url": "https://docs.example.com/a",
                    "raw_content": "Extracted content",
                }
            ],
            "failed_results": [],
            "usage": {"credits": 1},
            "request_id": "extract-request",
        }

    output_dir = tmp_path / "tavily-search"
    result = TavilyAdapter(api_key="test-tavily-key", http_post=fake_post).search_extract_pack(
        objective="Build a pack",
        queries=["docs"],
        output_dir=output_dir,
        include_domains=["docs.example.com"],
        max_search_results=1,
        extract_limit=1,
        mode="advanced",
    )

    assert result.extract_result_count == 1
    artifact_text = _artifact_text(output_dir)
    assert "test-tavily-key" not in artifact_text


def test_provider_adapter_rejects_explicit_unsafe_key_value() -> None:
    with pytest.raises(ProviderAdapterError, match="control characters"):
        TavilyAdapter(api_key="test-secret\x1fvalue")


def test_exa_adapter_uses_x_api_key_auth_without_persisting_key(tmp_path: Path) -> None:
    def fake_post(**kwargs: object) -> dict[str, object]:
        assert kwargs["headers"] == {"x-api-key": "test-exa-key"}
        return {
            "results": [
                {
                    "url": "https://docs.example.com/a",
                    "title": "A",
                    "text": "Exa content",
                }
            ],
            "requestId": "exa-request",
            "costDollars": {"total": 0.001},
        }

    output_dir = tmp_path / "exa-search"
    result = ExaAdapter(api_key="test-exa-key", http_post=fake_post).search_extract_pack(
        objective="Build a pack",
        queries=["docs"],
        output_dir=output_dir,
        include_domains=["docs.example.com"],
        max_search_results=1,
        extract_limit=1,
        mode="advanced",
    )

    assert result.extract_result_count == 1
    artifact_text = _artifact_text(output_dir)
    assert "test-exa-key" not in artifact_text


def _artifact_text(path: Path) -> str:
    return "\n".join(item.read_text(encoding="utf-8") for item in path.rglob("*") if item.is_file())
