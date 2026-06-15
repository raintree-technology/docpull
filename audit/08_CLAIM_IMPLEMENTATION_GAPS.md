# Claim / Implementation Gaps

## Resolved In Current Worktree

| Claim / gap | Current evidence | Status |
| --- | --- | --- |
| Runtime supports CLI help/version | `.venv/bin/python -m docpull --version` reports `docpull 4.3.0`; help works through the same import path | Resolved |
| Local editable install points at this checkout | `.venv/bin/python -m pip show docpull` reports editable project location `/Users/mb1/Code/raintree/docpull` | Resolved |
| `flat`/`short` naming aliases removed in 3.0 | CLI help now lists only `full` and `hierarchical`; config allows only those literals | Resolved |
| LLM profile JS policy is ambiguous | `profiles.py` documents that LLM mode skips JS-only pages by default; `--strict-js-required` remains the explicit fail-loud option | Resolved |
| Sphinx detected/tagged claim was weak | Sphinx now has an explicit static body extractor fixture and returns `source_type="sphinx"` | Resolved |
| Docusaurus detected/tagged claim was weak | Docusaurus now has an explicit static article extractor fixture and returns `source_type="docusaurus"` | Resolved |
| Common docs frameworks lacked fixture coverage | Static-region fixtures now cover MkDocs/Material, VitePress, Starlight, GitBook, ReadMe.io, and Redoc/Scalar-style pages | Resolved |
| SQLite output lacks local retrieval | `documents_fts` is created/backfilled and `search_sqlite_documents()` returns FTS hits | Resolved |
| Product boundary around "scraper" is implicit | `docs/scraping-boundary.md` defines browser-free scope and non-goals | Resolved |
| Plugin cache path/version docs drifted | `plugin/README.md` now points to `$XDG_DATA_HOME/docpull-mcp/docs/` / `~/.local/share/docpull-mcp/docs/` and says `4.0.0 or newer` | Resolved |

## Still Open

| Claim / gap | Current evidence | Status | Fix |
| --- | --- | --- | --- |
| Root TypeScript MCP status can confuse users | Python MCP is the package-supported path; root `mcp/` is separate/internal | Open architecture/documentation risk | Keep end-user docs focused on Python MCP or deliberately split/rename the TS surface |
| Optional JS rendering is expected by some "web scraper" users | README and boundary docs say browser-free default | Open strategic feature | Only add behind a separate extra with domain/budget controls |
| Authenticated/internal docs need stronger policy | Auth headers exist, but enterprise/private-doc mode is not a designed product surface | Open strategic feature | Add allowlists, redaction, audit logs, and scoped secret handling before marketing it |
| Live framework regressions remain valuable | Static fixtures cover common frameworks, but live pages can drift | Open feature work | Add curated live-regression captures and deeper data-feed extractors where static extraction underperforms |
