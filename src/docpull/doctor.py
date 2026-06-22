"""Diagnostic tool for verifying docpull installation and dependencies."""

from __future__ import annotations

import sys
from importlib import import_module
from pathlib import Path

from .rendering import (
    check_agent_browser_availability,
    check_e2b_sandbox_availability,
    check_vercel_sandbox_availability,
)

try:
    from rich.console import Console
    from rich.table import Table

    RICH_AVAILABLE = True
except ImportError:
    RICH_AVAILABLE = False
    Console = None  # type: ignore
    Table = None  # type: ignore


def check_dependency(
    module_name: str, package_name: str | None = None, optional: bool = False
) -> tuple[bool, str]:
    """
    Check if a Python module is importable.

    Args:
        module_name: Name of the module to import
        package_name: Display name of the package (defaults to module_name)
        optional: Whether this is an optional dependency

    Returns:
        Tuple of (success: bool, message: str)
    """
    display_name = package_name or module_name

    try:
        import_module(module_name)
        return True, f"[OK] {display_name}"
    except ImportError:
        if optional:
            return False, f"[WARN] {display_name} (optional - not installed)"
        else:
            return False, f"[MISSING] {display_name}"


def check_network() -> tuple[bool, str]:
    """
    Check basic network connectivity.

    Returns:
        Tuple of (success: bool, message: str)
    """
    try:
        import socket

        socket.gethostbyname("www.google.com")
        return True, "[OK] Network connectivity"
    except socket.gaierror:
        return False, "[FAIL] Network connectivity - DNS resolution failed"
    except Exception as e:
        return False, f"[WARN] Network connectivity - {str(e)}"


def check_output_dir(output_dir: Path | None = None) -> tuple[bool, str]:
    """
    Check if output directory is writable.

    Args:
        output_dir: Directory to check (defaults to ./docs)

    Returns:
        Tuple of (success: bool, message: str)
    """
    test_dir = output_dir or Path("./docs")

    try:
        test_dir.mkdir(parents=True, exist_ok=True)

        test_file = test_dir / ".docpull_test"
        test_file.write_text("test")
        test_file.unlink()

        return True, f"[OK] Output directory writable ({test_dir})"
    except PermissionError:
        return False, f"[FAIL] Output directory - permission denied ({test_dir})"
    except Exception as e:
        return False, f"[FAIL] Output directory - {str(e)} ({test_dir})"


def run_doctor(output_dir: Path | None = None, use_rich: bool = True) -> int:
    """
    Run diagnostic checks and display results.

    Args:
        output_dir: Output directory to check for writability
        use_rich: Whether to use rich formatting (if available)

    Returns:
        Exit code (0 if all core dependencies OK, 1 if any core dependency missing)
    """
    use_rich = use_rich and RICH_AVAILABLE

    print("Running docpull diagnostics...\n")

    core_checks = [
        ("bs4", "beautifulsoup4"),
        ("html2text", "html2text"),
        ("defusedxml", "defusedxml"),
        ("aiohttp", "aiohttp"),
        ("rich", "rich"),
        ("pydantic", "pydantic"),
    ]

    optional_checks = [
        ("aiohttp_socks", "aiohttp-socks", True),
        ("url_normalize", "url-normalize", True),
        ("trafilatura", "trafilatura", True),
        ("tiktoken", "tiktoken", True),
        ("mcp", "mcp", True),
    ]

    system_checks = [
        check_network(),
        check_output_dir(output_dir),
    ]
    external_tool_results = [
        check_agent_browser_availability(),
        check_vercel_sandbox_availability(),
        check_e2b_sandbox_availability(),
    ]

    core_results = [check_dependency(mod, pkg) for mod, pkg in core_checks]
    optional_results = [check_dependency(mod, pkg, opt) for mod, pkg, opt in optional_checks]

    all_checks = {
        "Core Dependencies": core_results,
        "Optional Dependencies": optional_results,
        "Optional External Tools": external_tool_results,
        "System": system_checks,
    }

    if use_rich:
        console = Console()

        for category, results in all_checks.items():
            table = Table(title=category, show_header=False, box=None)
            table.add_column("Status", style="bold")

            for success, message in results:
                style = "green" if success else ("yellow" if "[WARN]" in message else "red")
                table.add_row(message, style=style)

            console.print(table)
            console.print()
    else:
        for category, results in all_checks.items():
            print(f"{category}:")
            for _success, message in results:
                print(f"  {message}")
            print()

    core_failed = any(not success for success, _ in core_results)

    if core_failed:
        print("\nWARNING: Some core dependencies are missing!")
        print("\nRecommended fixes:")
        print("  1. For pipx users: pipx reinstall docpull --force")
        print("  2. For pip users: pip install --upgrade --force-reinstall docpull")
        print("  3. For development: pip install -e .[dev]")
        return 1
    else:
        print("\nAll core dependencies installed correctly!")

        optional_missing = [msg for success, msg in optional_results if not success]
        external_missing = [msg for success, msg in external_tool_results if not success]
        if optional_missing or external_missing:
            print("\nOptional features available:")
            print("  - Proxy support: pip install docpull[proxy]")
            print("  - URL normalization helpers: pip install docpull[normalize]")
            print("  - Browser rendering: install an agent-browser compatible CLI")
            print("    or set DOCPULL_AGENT_BROWSER_BIN to its executable path")
            print("  - Cloud rendering: install the Vercel Sandbox CLI or `docpull[e2b]`")
            print("    and configure Vercel auth or E2B_API_KEY")
            print("    Optional: set DOCPULL_E2B_TEMPLATE for a prebuilt E2B renderer image")
            print("  - All optional features: pip install docpull[all]")

        return 0


if __name__ == "__main__":
    sys.exit(run_doctor())
