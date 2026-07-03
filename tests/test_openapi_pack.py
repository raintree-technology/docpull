"""Tests for local OpenAPI spec packs."""

from __future__ import annotations

import json
from pathlib import Path

from docpull.cli import main
from docpull.context_packs.openapi import build_openapi_pack
from docpull.output_contract import validate_pack_contract
from docpull.pack_reader import load_pack


def _openapi_spec() -> dict[str, object]:
    return {
        "openapi": "3.1.0",
        "info": {"title": "Example API", "version": "2026-07-01"},
        "paths": {
            "/users": {
                "get": {
                    "summary": "List users",
                    "operationId": "listUsers",
                    "tags": ["Users"],
                    "parameters": [
                        {
                            "name": "limit",
                            "in": "query",
                            "required": False,
                            "schema": {"type": "integer"},
                            "description": "Maximum result count.",
                        }
                    ],
                    "responses": {
                        "200": {
                            "description": "Users response.",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "array",
                                        "items": {"$ref": "#/components/schemas/User"},
                                    }
                                }
                            },
                        }
                    },
                }
            }
        },
        "components": {
            "schemas": {
                "User": {
                    "type": "object",
                    "required": ["id"],
                    "properties": {
                        "id": {"type": "string", "description": "Stable user id."},
                        "email": {"type": "string", "description": "Contact email."},
                    },
                }
            }
        },
    }


def test_build_openapi_pack_writes_v3_records(tmp_path: Path) -> None:
    spec_path = tmp_path / "openapi.json"
    spec_path.write_text(json.dumps(_openapi_spec()), encoding="utf-8")
    pack_dir = tmp_path / "pack"

    result = build_openapi_pack(spec_path, output_dir=pack_dir)

    assert result["validation"]["status"] == "pass"
    assert validate_pack_contract(pack_dir, level="raw")["status"] == "pass"
    index = json.loads((pack_dir / "openapi.index.json").read_text(encoding="utf-8"))
    assert index["operation_count"] == 1
    assert index["schema_count"] == 1
    assert index["operations"][0]["operation_id"] == "listUsers"
    assert (pack_dir / "openapi.spec.json").exists()
    assert (pack_dir / "OPENAPI.md").exists()

    pack = load_pack(pack_dir)
    assert len(pack.documents) == 2
    assert {record.source_type for record in pack.documents} == {"openapi_operation", "openapi_schema"}
    operation = next(record for record in pack.documents if record.source_type == "openapi_operation")
    assert operation.route["name"] == "local-openapi-parse"
    assert operation.metadata["method"] == "GET"
    assert operation.metadata["path"] == "/users"
    assert pack.record_citation_id(operation) == "S1.1"


def test_openapi_pack_cli_json_output(tmp_path: Path, capsys) -> None:
    spec_path = tmp_path / "openapi.yaml"
    spec_path.write_text(
        """
openapi: 3.1.0
info:
  title: YAML API
paths:
  /health:
    get:
      summary: Health check
      responses:
        "200":
          description: OK
""",
        encoding="utf-8",
    )
    pack_dir = tmp_path / "pack"

    assert main(["openapi-pack", str(spec_path), "-o", str(pack_dir), "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["workflow"] == "openapi-pack"
    assert payload["summary"]["operation_count"] == 1
    assert payload["validation"]["status"] == "pass"
