"""
Task Manager for proper asyncio task lifecycle management
Prevents memory leaks from uncancelled tasks
"""

import asyncio
import logging
from typing import Set, Coroutine, Any
import weakref

_logger = logging.getLogger(__name__)

class TaskManager:
    """Manages asyncio tasks with proper lifecycle handling"""

    def __init__(self, name: str = "TaskManager"):
        self.name = name
        self._tasks: Set[asyncio.Task] = set()
        self._shutdown = False

    def create_task(self, coro: Coroutine[Any, Any, Any], name: str = "unnamed") -> asyncio.Task:
        """Create a task with proper lifecycle management"""
        if self._shutdown:
            _logger.warning(f"{self.name}: Task creation blocked - shutting down")
            raise RuntimeError("TaskManager is shutting down")

        task = asyncio.create_task(coro)
        task.set_name(f"{self.name}:{name}")
        self._tasks.add(task)

        # Remove task from set when done
        task.add_done_callback(self._tasks.discard)

        _logger.debug(f"{self.name}: Created task '{name}' ({len(self._tasks)} active)")
        return task

    async def cancel_all(self, timeout: float = 5.0) -> None:
        """Cancel all active tasks with timeout"""
        if not self._tasks:
            return

        _logger.info(f"{self.name}: Cancelling {len(self._tasks)} tasks")

        tasks_to_cancel = list(self._tasks)

        # Cancel all tasks
        for task in tasks_to_cancel:
            if not task.done():
                task.cancel()

        # Wait for cancellation with timeout
        try:
            await asyncio.wait_for(
                asyncio.gather(*tasks_to_cancel, return_exceptions=True),
                timeout=timeout
            )
        except asyncio.TimeoutError:
            _logger.warning(f"{self.name}: Task cancellation timed out after {timeout}s")

        self._tasks.clear()
        _logger.info(f"{self.name}: All tasks cancelled")

    def get_active_count(self) -> int:
        """Get count of active tasks"""
        # Clean up completed tasks
        self._tasks = {task for task in self._tasks if not task.done()}
        return len(self._tasks)

    def get_task_names(self) -> list[str]:
        """Get names of active tasks"""
        return [task.get_name() for task in self._tasks if not task.done()]

    async def shutdown(self) -> None:
        """Shutdown the task manager"""
        _logger.info(f"{self.name}: Shutting down")
        self._shutdown = True
        await self.cancel_all()
        _logger.info(f"{self.name}: Shutdown complete")

    def __len__(self) -> int:
        """Return number of active tasks"""
        return self.get_active_count()