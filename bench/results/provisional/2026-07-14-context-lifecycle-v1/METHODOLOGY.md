# Methodology

The suite uses two small, benchmark-authored synthetic documentation packs. All source content is redistribution-safe. The fixture represents one unchanged page, one changed page, one removed page, and one added page.

Every operation runs through the installed public DocPull CLI. Checks marked `network=disabled` run with all HTTP proxy variables pointed at a closed loopback port. The zero-budget cloud check supplies a fake credential and succeeds only when the route is rejected before a render artifact is written.

Pass/fail assertions are deterministic and use no LLM judge. Temporary fixture directories, fixture bodies, and environment credentials are excluded from reports.

This lane evaluates persistent context artifacts, not raw fetch coverage. It must not be aggregated with the live fixed-URL extraction score.
