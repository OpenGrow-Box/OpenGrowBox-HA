"""
OpenGrowBox Premium WebSocket Reconnection Module

Handles WebSocket reconnection logic with exponential backoff.
This module provides a mixin class that can be used with the main WebSocket client.
"""

import asyncio
import logging
import secrets
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ...utils.Premium.SecureWebSocketClient import OGBWebSocketConManager

_LOGGER = logging.getLogger(__name__)


class OGBPremWebSocketReconnectMixin:
    """
    Mixin class providing reconnection functionality for WebSocket clients.

    This mixin expects the following attributes on the parent class:
    - _reconnection_lock: asyncio.Lock
    - _connection_lock: asyncio.Lock
    - _reconnection_in_progress: bool
    - _should_reconnect: bool
    - reconnect_attempts: int
    - max_reconnect_attempts: int
    - ws_connected: bool
    - ws_room: str
    - sio: socketio.AsyncClient
    - ogbevents: EventManager

    And the following methods:
    - _stop_keepalive()
    - _start_keepalive()
    - _connect_websocket()
    - _request_session_key()
    - session_restore()
    """

    async def _handle_connection_loss(self, reason: str = "unknown"):
        """
        Handle connection loss with proper locking.

        Args:
            reason: Reason for the connection loss
        """
        async with self._reconnection_lock:
            if self._reconnection_in_progress:
                _LOGGER.debug(f"Reconnection already in progress for {self.ws_room}")
                return

            self._reconnection_in_progress = True

        try:
            # Update states (outside lock to avoid blocking)
            self.ws_connected = False

            # Stop keep-alive
            await self._stop_keepalive()

            # Disconnect cleanly if still connected
            try:
                if self.sio.connected:
                    await self.sio.disconnect()
            except Exception:
                pass

            # Start single reconnection if enabled
            if self._should_reconnect:
                _LOGGER.warning(
                    f"Connection lost for {self.ws_room} ({reason}), starting reconnection"
                )
                async with self._reconnection_lock:
                    if not self.reconnect_task or self.reconnect_task.done():
                        self.reconnect_task = asyncio.create_task(
                            self._unified_reconnect_loop()
                        )
            else:
                _LOGGER.warning(
                    f"Connection lost for {self.ws_room} ({reason}), reconnection disabled"
                )
                async with self._reconnection_lock:
                    self._reconnection_in_progress = False
        except Exception as e:
            _LOGGER.error(f"Error in connection loss handler for {self.ws_room}: {e}")
            async with self._reconnection_lock:
                self._reconnection_in_progress = False

    async def force_reconnect(self) -> bool:
        """
        Force a reconnection attempt.

        Returns:
            True if reconnection was initiated successfully
        """
        try:
            if self._reconnection_in_progress:
                _LOGGER.warning(f"Reconnection already in progress for {self.ws_room}")
                return True

            if self.is_connected():
                _LOGGER.warning(
                    f"Already connected for {self.ws_room}, no need to reconnect"
                )
                return True

            _LOGGER.warning(f"Forcing reconnection for {self.ws_room}")
            await self._handle_connection_loss("force_reconnect")

            # Wait briefly for reconnection to start
            await asyncio.sleep(2)
            return True

        except Exception as e:
            _LOGGER.error(f"Force reconnect error for {self.ws_room}: {e}")
            return False

    async def _unified_reconnect_loop(self):
        """
        Unified reconnection loop with optimized exponential backoff timing.

        Backoff strategy:
        - Attempt 1: 2 seconds
        - Attempts 2-3: Slow growth (1.2x multiplier)
        - Attempts 4+: Faster growth (1.5x multiplier) up to 60 seconds max
        """
        async with self._reconnection_lock:
            # Check if already reconnecting to prevent race conditions
            if self._reconnection_in_progress:
                _LOGGER.debug(f"Reconnection already in progress for {self.ws_room}")
                return

            self._reconnection_in_progress = True

        try:
            base_delay = 2  # Start with 2 seconds
            max_delay = 60  # 1 minute max

            while (
                self._should_reconnect
                and self.reconnect_attempts < self.max_reconnect_attempts
                and not self.ws_connected
            ):

                self.reconnect_attempts += 1

                # Calculate delay with conservative exponential backoff
                delay = self._calculate_reconnect_delay(
                    self.reconnect_attempts, base_delay, max_delay
                )

                # Add small jitter (5%)
                jitter = delay * 0.05 * (secrets.randbelow(100) / 100)
                total_delay = delay + jitter

                _LOGGER.warning(
                    f"Reconnect attempt {self.reconnect_attempts}/{self.max_reconnect_attempts} "
                    f"for {self.ws_room} in {total_delay:.1f}s"
                )

                await asyncio.sleep(total_delay)

                # Check if we should still reconnect
                if not self._should_reconnect or self.ws_connected:
                    break

                try:
                    async with self._connection_lock:
                        # Double check connection state
                        if self.ws_connected:
                            _LOGGER.warning(
                                f"Already connected during reconnect attempt for {self.ws_room}"
                            )
                            break

                        # Ensure clean state
                        if hasattr(self, "sio") and self.sio.connected:
                            try:
                                await self.sio.disconnect()
                                await asyncio.sleep(0.3)
                            except Exception:
                                pass

                    # Try to get fresh session if needed
                    if not self._session_key or not self._session_id:
                        _LOGGER.warning(
                            f"Requesting new session for {self.ws_room} reconnect"
                        )
                        if not await self._request_session_key():
                            _LOGGER.error(
                                f"Failed to get session key for {self.ws_room} reconnect"
                            )
                            continue

                    # Try to reconnect
                    if await self._connect_websocket():
                        _LOGGER.warning(f"Reconnect successful for {self.ws_room}")
                        self.reconnect_attempts = 0  # Reset on success
                        await self._start_keepalive()
                        return
                    else:
                        _LOGGER.warning(
                            f"Reconnection Error Try Session Restore for {self.ws_room}"
                        )
                        await self.session_restore()

                except Exception as e:
                    _LOGGER.error(
                        f"Reconnect attempt {self.reconnect_attempts} failed for {self.ws_room}: {e}"
                    )

            _LOGGER.debug(
                f"{self.ws_room} Max reconnect attempts:{self.reconnect_attempts} Reached"
            )

            if self.reconnect_attempts >= self.max_reconnect_attempts:
                await self.ogbevents.emit(
                    "LogForClient",
                    f"Connection lost and max reconnect attempts reached for {self.ws_room}. Please try logging in again.",
                    haEvent=True,
                )
        finally:
            async with self._reconnection_lock:
                self._reconnection_in_progress = False

    @staticmethod
    def _calculate_reconnect_delay(
        attempt: int, base_delay: float, max_delay: float
    ) -> float:
        """
        Calculate reconnection delay with conservative exponential backoff.

        Args:
            attempt: Current attempt number (1-based)
            base_delay: Base delay in seconds
            max_delay: Maximum delay in seconds

        Returns:
            Delay in seconds
        """
        if attempt == 1:
            return base_delay  # First attempt: 2s
        elif attempt <= 3:
            # Slower growth for attempts 2-3: 2s, 2.4s, 2.88s
            return base_delay * (1.2 ** (attempt - 1))
        else:
            # Faster growth after 3rd attempt, capped at max_delay
            return min(base_delay * (1.5 ** (attempt - 3)) * 2.88, max_delay)
