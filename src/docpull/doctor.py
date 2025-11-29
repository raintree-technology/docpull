"""Diagnostic tool for verifying docpull installation and dependencies."""

import sys
from importlib import import_module
from pathlib import Path
from typing import Optional

try:
    from rich.console import Console
    from rich.table import Table

    RICH_AVAILABLE = True
except ImportError:
    RICH_AVAILABLE = False
    Console = None  # type: ignore
    Table = None  # type: ignore


def check_dependency(
    module_name: str, package_name: Optional[str] = None, optional: bool = False
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

        # Try to resolve a common DNS name
        socket.gethostbyname("www.google.com")
        return True, "[OK] Network connectivity"
    except socket.gaierror:
        return False, "[FAIL] Network connectivity - DNS resolution failed"
    except Exception as e:
        return False, f"[WARN] Network connectivity - {str(e)}"


def check_output_dir(output_dir: Optional[Path] = None) -> tuple[bool, str]:
    """
    Check if output directory is writable.

    Args:
        output_dir: Directory to check (defaults to ./docs)

    Returns:
        Tuple of (success: bool, message: str)
    """
    test_dir = output_dir or Path("./docs")

    try:
        # Create directory if it doesn't exist
        test_dir.mkdir(parents=True, exist_ok=True)

        # Try to write a test file
        test_file = test_dir / ".docpull_test"
        test_file.write_text("test")
        test_file.unlink()

        return True, f"[OK] Output directory writable ({test_dir})"
    except PermissionError:
        return False, f"[FAIL] Output directory - permission denied ({test_dir})"
    except Exception as e:
        return False, f"[FAIL] Output directory - {str(e)} ({test_dir})"


def run_doctor(output_dir: Optional[Path] = None, use_rich: bool = True) -> int:
    """
    Run diagnostic checks and display results.

    Args:
        output_dir: Output directory to check for writability
        use_rich: Whether to use rich formatting (if available)

    Returns:
        Exit code (0 if all core dependencies OK, 1 if any core dependency missing)
    """
    # Determine if we can use rich formatting
    use_rich = use_rich and RICH_AVAILABLE

    print("Running docpull diagnostics...\n")

    # Core dependencies
    core_checks = [
        ("requests", "requests"),
        ("bs4", "beautifulsoup4"),
        ("html2text", "html2text"),
        ("defusedxml", "defusedxml"),
        ("aiohttp", "aiohttp"),
        ("rich", "rich"),
    ]

    # Optional dependencies
    optional_checks = [
        ("yaml", "pyyaml", True),
        ("playwright.async_api", "playwright", True),
    ]

    # Other checks
    system_checks = [
        check_network(),
        check_output_dir(output_dir),
    ]

    # Run core dependency checks
    core_results = [check_dependency(mod, pkg) for mod, pkg in core_checks]
    optional_results = [check_dependency(mod, pkg, opt) for mod, pkg, opt in optional_checks]

    all_checks = {
        "Core Dependencies": core_results,
        "Optional Dependencies": optional_results,
        "System": system_checks,
    }

    # Display results
    if use_rich:
        console = Console()

        for category, results in all_checks.items():
            table = Table(title=category, show_header=False, box=None)
            table.add_column("Status", style="bold")

            for success, message in results:
                style = "green" if success else ("yellow" if "optional" in message else "red")
                table.add_row(message, style=style)

            console.print(table)
            console.print()
    else:
        # Fallback to plain text
        for category, results in all_checks.items():
            print(f"{category}:")
            for _success, message in results:
                print(f"  {message}")
            print()

    # Check if any core dependencies failed
    core_failed = any(not success for success, _ in core_results)

    # Print summary
    if core_failed:
        print("\nWARNING: Some core dependencies are missing!")
        print("\nRecommended fixes:")
        print("  1. For pipx users: pipx reinstall docpull --force")
        print("  2. For pip users: pip install --upgrade --force-reinstall docpull")
        print("  3. For development: pip install -e .[dev]")
        return 1
    else:
        print("\nAll core dependencies installed correctly!")

        # Check if optional dependencies are missing
        optional_missing = [msg for success, msg in optional_results if not success]
        if optional_missing:
            print("\nOptional features available:")
            print("  - YAML config support: pip install docpull[yaml]")
            print("  - JavaScript rendering: pip install docpull[js]")
            print("  - All optional features: pip install docpull[all]")

        return 0


if __name__ == "__main__":
    sys.exit(run_doctor())
