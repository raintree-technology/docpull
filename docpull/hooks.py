"""Plugin/hook system for custom processing."""

import importlib.util
import inspect
import logging
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)


class HookType(Enum):
    """Types of hooks that can be registered."""

    PRE_FETCH = "pre_fetch"  # Before fetching a URL
    POST_FETCH = "post_fetch"  # After fetching, before saving
    POST_PROCESS = "post_process"  # After all files fetched
    FILTER = "filter"  # Decide whether to fetch a URL


class HookResult:
    """Result from a hook execution."""

    def __init__(
        self, should_continue: bool = True, modified_data: Optional[Any] = None, message: Optional[str] = None
    ):
        """Initialize hook result.

        Args:
            should_continue: Whether to continue processing
            modified_data: Modified data to use instead of original
            message: Optional message/log
        """
        self.should_continue = should_continue
        self.modified_data = modified_data
        self.message = message


class Hook:
    """Base class for hooks."""

    def __init__(self, name: str):
        """Initialize hook.

        Args:
            name: Hook name
        """
        self.name = name

    def execute(self, context: dict[str, Any]) -> HookResult:
        """Execute the hook.

        Args:
            context: Context dict with hook-specific data

        Returns:
            HookResult
        """
        raise NotImplementedError


class HookManager:
    """Manage and execute hooks/plugins."""

    def __init__(self):
        """Initialize hook manager."""
        self.hooks: dict[HookType, list[Hook]] = {hook_type: [] for hook_type in HookType}

    def register_hook(self, hook_type: HookType, hook: Hook):
        """Register a hook.

        Args:
            hook_type: Type of hook
            hook: Hook instance
        """
        self.hooks[hook_type].append(hook)
        logger.info(f"Registered {hook_type.value} hook: {hook.name}")

    def register_function(self, hook_type: HookType, func: Callable, name: Optional[str] = None):
        """Register a function as a hook.

        Args:
            hook_type: Type of hook
            func: Function to call
            name: Optional hook name
        """
        name = name or func.__name__

        class FunctionHook(Hook):
            def execute(self, context: dict[str, Any]) -> HookResult:
                try:
                    result = func(context)
                    if isinstance(result, HookResult):
                        return result
                    elif isinstance(result, bool):
                        return HookResult(should_continue=result)
                    elif isinstance(result, dict):
                        return HookResult(modified_data=result)
                    else:
                        return HookResult()
                except Exception as e:
                    logger.error(f"Hook {name} failed: {e}", exc_info=True)
                    return HookResult(should_continue=True)

        self.register_hook(hook_type, FunctionHook(name))

    def load_from_file(self, file_path: Path):
        """Load hooks from a Python file.

        Args:
            file_path: Path to Python file with hooks
        """
        file_path = Path(file_path)

        if not file_path.exists():
            raise FileNotFoundError(f"Hook file not found: {file_path}")

        try:
            # Load module
            spec = importlib.util.spec_from_file_location("custom_hooks", file_path)
            if not spec or not spec.loader:
                raise ImportError(f"Could not load {file_path}")

            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)

            # Find hook functions
            for name, obj in inspect.getmembers(module):
                if inspect.isfunction(obj):
                    # Check for hook type annotation
                    if hasattr(obj, "_hook_type"):
                        hook_type = obj._hook_type
                        self.register_function(hook_type, obj, name)
                    # Check for naming convention
                    elif name.startswith("pre_fetch_"):
                        self.register_function(HookType.PRE_FETCH, obj, name)
                    elif name.startswith("post_fetch_"):
                        self.register_function(HookType.POST_FETCH, obj, name)
                    elif name.startswith("post_process_"):
                        self.register_function(HookType.POST_PROCESS, obj, name)
                    elif name.startswith("filter_"):
                        self.register_function(HookType.FILTER, obj, name)

                elif isinstance(obj, Hook) and hasattr(obj, "_hook_type"):
                    # Hook instance
                    self.register_hook(obj._hook_type, obj)

            logger.info(f"Loaded hooks from {file_path}")

        except Exception as e:
            logger.error(f"Failed to load hooks from {file_path}: {e}", exc_info=True)
            raise

    def execute_hooks(self, hook_type: HookType, context: dict[str, Any]) -> HookResult:
        """Execute all hooks of a given type.

        Args:
            hook_type: Type of hooks to execute
            context: Context to pass to hooks

        Returns:
            Combined HookResult
        """
        if hook_type not in self.hooks or not self.hooks[hook_type]:
            return HookResult()

        should_continue = True
        modified_data = None
        messages = []

        for hook in self.hooks[hook_type]:
            try:
                result = hook.execute(context)

                if not result.should_continue:
                    should_continue = False

                if result.modified_data is not None:
                    modified_data = result.modified_data
                    # Update context for next hook
                    if isinstance(context, dict) and isinstance(modified_data, dict):
                        context.update(modified_data)

                if result.message:
                    messages.append(f"{hook.name}: {result.message}")

            except Exception as e:
                logger.error(f"Hook {hook.name} failed: {e}", exc_info=True)
                # Continue with other hooks

        return HookResult(
            should_continue=should_continue,
            modified_data=modified_data,
            message="\n".join(messages) if messages else None,
        )


def hook(hook_type: HookType):
    """Decorator to mark a function as a hook.

    Args:
        hook_type: Type of hook

    Example:
        @hook(HookType.PRE_FETCH)
        def my_hook(context):
            url = context['url']
            # Process...
            return True  # Continue
    """

    def decorator(func):
        func._hook_type = hook_type
        return func

    return decorator
