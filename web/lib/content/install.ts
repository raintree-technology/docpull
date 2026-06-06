export const installCommand = "pip install docpull";

export const altInstallMethods = [
  { label: "pipx", command: "pipx install docpull" },
  { label: "uv", command: "uv tool install docpull" },
  { label: "+proxy", command: "pip install docpull[proxy]" },
  { label: "+all", command: "pip install docpull[all]" },
] as const;

export const claudePluginUrl =
  "https://github.com/raintree-technology/docpull/tree/main/plugin";

export const claudePluginInstall = `pip install 'docpull[mcp]'
/plugin marketplace add raintree-technology/docpull
/plugin install docpull@docpull`;

export const mcpSetups = [
  {
    id: "claude-code",
    label: "Claude Code",
    brand: "anthropic",
    mode: "MCP",
    note: "Add the local stdio server directly to Claude Code.",
    code: `pip install 'docpull[mcp]'
claude mcp add --transport stdio --scope user docpull -- docpull mcp`,
  },
  {
    id: "cursor",
    label: "Cursor",
    brand: "cursor",
    mode: "MCP",
    note: "This repo includes .cursor/mcp.json and a matching docpull research rule.",
    code: `pip install 'docpull[mcp]'
{
  "mcpServers": {
    "docpull": {
      "type": "stdio",
      "command": "docpull",
      "args": ["mcp"]
    }
  }
}`,
  },
  {
    id: "codex",
    label: "Codex",
    brand: "openai",
    mode: "MCP",
    note: "Use shared MCP config, or .codex/config.toml in a trusted repo; AGENTS.md carries matching guidance.",
    code: `pip install 'docpull[mcp]'
codex mcp add docpull -- docpull mcp`,
  },
] as const;
