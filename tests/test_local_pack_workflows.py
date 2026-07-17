"""Local-pack replay must never silently fall back to network acquisition."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from docpull.context_packs import brand, policy_pack, product, relationship, styleguide, visuals
from docpull.context_packs.common import PageSnapshot


def _local_pack(root: Path) -> Path:
    root.mkdir()
    records = [
        {
            "schema_version": 3,
            "document_id": "doc_home",
            "url": "https://example.com/",
            "title": "Example Product",
            "content": "# Example Product\n\nOfficial API support and unlimited monitoring.\n",
            "content_hash": hashlib.sha256(
                b"# Example Product\n\nOfficial API support and unlimited monitoring.\n"
            ).hexdigest(),
            "metadata": {"entity_name": "Example"},
            "extraction": {},
        },
        {
            "schema_version": 3,
            "document_id": "doc_pricing",
            "url": "https://example.com/pricing",
            "title": "Pricing",
            "content": "# Pricing\n\nPro plan $20 per month. 14-day free trial.\n",
            "content_hash": hashlib.sha256(
                b"# Pricing\n\nPro plan $20 per month. 14-day free trial.\n"
            ).hexdigest(),
            "metadata": {},
            "extraction": {},
        },
    ]
    (root / "documents.ndjson").write_text(
        "".join(json.dumps(record) + "\n" for record in records),
        encoding="utf-8",
    )
    (root / "corpus.manifest.json").write_text(
        json.dumps({"schema_version": 3, "records": []}),
        encoding="utf-8",
    )
    return root


def _network_forbidden(*args, **kwargs):  # type: ignore[no-untyped-def]
    raise AssertionError("local pack mode attempted network access")


def test_all_compatible_pack_workflows_replay_without_network(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pack = _local_pack(tmp_path / "input")
    monkeypatch.setattr(brand, "fetch_pages_blocking", _network_forbidden)
    monkeypatch.setattr(brand, "fetch_asset_blocking", _network_forbidden)
    monkeypatch.setattr(product, "fetch_pages_blocking", _network_forbidden)
    monkeypatch.setattr(styleguide, "fetch_pages_blocking", _network_forbidden)
    monkeypatch.setattr(styleguide, "_fetch_css_blocking", _network_forbidden)
    monkeypatch.setattr(policy_pack, "fetch_pages_blocking", _network_forbidden)
    monkeypatch.setattr(visuals, "fetch_pages_blocking", _network_forbidden)
    monkeypatch.setattr(visuals, "fetch_asset_blocking", _network_forbidden)
    monkeypatch.setattr(relationship, "fetch_pages_blocking", _network_forbidden)

    results = [
        brand.build_brand_pack(str(pack), output_dir=tmp_path / "brand"),
        product.build_product_pack(str(pack), mode="site", output_dir=tmp_path / "product"),
        policy_pack.build_policy_pack(str(pack), output_dir=tmp_path / "policy"),
        styleguide.build_styleguide_pack(str(pack), output_dir=tmp_path / "styleguide"),
        visuals.build_image_pack(str(pack), output_dir=tmp_path / "visual"),
        relationship.build_relationship_pack([str(pack)], output_dir=tmp_path / "relationship"),
    ]
    assert all(result["provider"] == "local" for result in results)
    assert all(
        (tmp_path / name / "workflow.result.json").is_file()
        for name in (
            "brand",
            "product",
            "policy",
            "styleguide",
            "visual",
            "relationship",
        )
    )


def test_product_trial_and_testimonial_guards_are_deterministic() -> None:
    page = PageSnapshot(
        url="https://example.com/pricing",
        title="Pricing",
        html=(
            '<section><p class="price">14-day free trial $0</p>'
            '<p class="price">Pro $25 per month</p></section>'
            '<blockquote class="testimonial"><p>We love the unlimited API support feature.</p></blockquote>'
            '<section class="features"><p>Includes unlimited API monitoring.</p></section>'
        ),
        markdown=(
            "14-day free trial $0\n\nPro $25 per month\n\n"
            "We love the unlimited API support feature.\n\n"
            "Includes unlimited API monitoring.\n"
        ),
        metadata={},
        extraction={},
    )
    rows = product._pricing_rows(page, [page])
    trial = next(row for row in rows if row["trial"])
    assert trial["price"] is None
    assert trial["currency"] is None
    assert any(row["price"] == 25.0 and row["currency"] == "USD" for row in rows)
    features = product._feature_bullets(page, [page])
    assert [item["text"] for item in features] == ["Includes unlimited API monitoring."]


def test_structured_zero_price_trial_is_not_a_subscription_price() -> None:
    page = PageSnapshot(
        url="https://example.com/",
        title="Example",
        html=(
            '<script type="application/ld+json">'
            '{"@type":"Product","name":"Example","offers":{"@type":"Offer",'
            '"price":"0","priceCurrency":"USD","description":"Free trial available"}}'
            "</script>"
        ),
        markdown="# Example\n\nTry Example free for 7 days.\n",
        metadata={"page_role": "home"},
        extraction={},
    )

    offer = product._jsonld_products(page, [page])[0]["offers"][0]

    assert offer["price"] is None
    assert offer["currency"] is None
    assert offer["trial"]["duration_days"] == 7
    assert offer["evidence"]["excerpt"] == "Try Example free for 7 days."
    assert offer["evidence"]["evidence_span"]["exact_text"] == offer["evidence"]["excerpt"]


def test_plain_language_annual_cadence_is_normalized() -> None:
    assert product._billing_from_text("The plan costs $99 a year.") == "yearly"
    assert product._billing_interval("The plan costs $99 each year.") == {
        "unit": "year",
        "count": 1,
    }
