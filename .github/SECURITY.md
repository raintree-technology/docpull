# Security Policy

## Security Features

- **HTTPS-only**: HTTP URLs rejected, SSL verification enabled
- **Path traversal protection**: Output paths validated, filenames sanitized
- **Size limits**: 50MB per file, configurable total limits
- **XXE protection**: Uses defusedxml for safe XML parsing
- **SSRF protection**: Blocks private IPs, localhost, cloud metadata endpoints
- **Timeouts**: 30s connection, 5min download limits
- **Content-type validation**: Only accepts HTML/XML content
- **Playwright sandboxing**: Headless mode, resource blocking, isolated contexts

## Reporting Vulnerabilities

Report security issues to **support@raintree.technology**.

Include: description, reproduction steps, potential impact.

Do not open public GitHub issues for security vulnerabilities.

## Supply Chain Security

- Pinned dependencies in pyproject.toml
- Automated scanning with Bandit and pip-audit
- All dependencies actively maintained
