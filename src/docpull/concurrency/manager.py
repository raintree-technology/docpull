"""Thread pool manager for CPU-bound operations."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Optional, TypeVar

T = TypeVar("T")


class ConcurrencyManager:
    """
    Manages a ThreadPoolExecutor for CPU-bound work in async contexts.

    Use this to offload CPU-intensive operations (like HTML parsing,
    metadata extraction, hash computation) to avoid blocking the event loop.

    Example:
        async with ConcurrencyManager(max_workers=4) as manager:
            # Offload CPU-bound BeautifulSoup parsing
            soup = await manager.run_cpu_bound(BeautifulSoup, html, "html.parser")

            # Offload metadata extraction
            metadata = await manager.run_cpu_bound(extractor.extract, html, url)
    """

    def __init__(self, max_workers: int = 4) -> None:
        """
        Initialize the concurrency manager.

        Args:
            max_workers: Number of thread pool workers. Defaults to 4.
                        Consider CPU core count for optimal value.
        """
        self.max_workers = max_workers
        self._executor: Optional[ThreadPoolExecutor] = None

    @property
    def executor(self) -> ThreadPoolExecutor:
        """Get or create the thread pool executor."""
        if self._executor is None:
            self._executor = ThreadPoolExecutor(
                max_workers=self.max_workers,
                thread_name_prefix="docpull-cpu-",
            )
        return self._executor

    async def run_cpu_bound(
        self,
        func: Callable[..., T],
        *args: Any,
        **kwargs: Any,
    ) -> T:
        """
        Run a CPU-bound function in the thread pool.

        This avoids blocking the async event loop during heavy computation.

        Args:
            func: The function to execute
            *args: Positional arguments for the function
            **kwargs: Keyword arguments for the function

        Returns:
            The result of the function call

        Example:
            result = await manager.run_cpu_bound(
                heavy_computation,
                data,
                option=True
            )
        """
        loop = asyncio.get_running_loop()

        # Wrap function call to handle kwargs
        if kwargs:

            def wrapper() -> T:
                return func(*args, **kwargs)

            return await loop.run_in_executor(self.executor, wrapper)
        else:
            return await loop.run_in_executor(self.executor, func, *args)

    def shutdown(self, wait: bool = True) -> None:
        """
        Shutdown the thread pool executor.

        Args:
            wait: If True, wait for pending tasks to complete.
                  If False, cancel pending tasks immediately.
        """
        if self._executor is not None:
            self._executor.shutdown(wait=wait)
            self._executor = None

    async def __aenter__(self) -> "ConcurrencyManager":
        """Enter async context."""
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Exit async context and shutdown executor."""
        self.shutdown(wait=True)

    def __enter__(self) -> "ConcurrencyManager":
        """Enter sync context."""
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Exit sync context and shutdown executor."""
        self.shutdown(wait=True)
