import json

from docpull.accounting import RunAccounting, write_run_accounting


def test_write_run_accounting_links_existing_pack_artifacts(tmp_path):
    agent_context = tmp_path / "AGENT_CONTEXT.md"
    agent_context.write_text("# Pack\n\nUse the records first.\n", encoding="utf-8")
    pack_path = tmp_path / "parallel.pack.json"
    pack_path.write_text(
        json.dumps({"artifacts": {"agent_context": "AGENT_CONTEXT.md"}}),
        encoding="utf-8",
    )

    accounting_path = write_run_accounting(
        tmp_path,
        RunAccounting(
            budget_limit_usd=0,
            estimated_paid_cost_usd=0,
            command="parallel context-pack",
        ),
    )

    assert accounting_path.name == "run.accounting.json"
    pack = json.loads(pack_path.read_text(encoding="utf-8"))
    assert pack["artifacts"]["accounting"] == "run.accounting.json"
    agent_context_text = agent_context.read_text(encoding="utf-8")
    assert "## Run Accounting" in agent_context_text
    assert "`run.accounting.json`" in agent_context_text
