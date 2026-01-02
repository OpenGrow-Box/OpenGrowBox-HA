"""
OpenGrowBox Premium WebSocket Events Module

Event handler definitions that can be registered with the WebSocket client.
These are utility functions, not a mixin - they're called from the main client.

This module contains the event handler logic extracted from SecureWebSocketClient.py
to improve maintainability and testability.
"""

import asyncio
import logging
import time
from typing import Any, Callable, Dict, Optional

_LOGGER = logging.getLogger(__name__)


class OGBPremWebSocketEventHandlers:
    """
    Container for WebSocket event handler factory methods.

    These create handler functions that can be registered with socket.io.
    The handlers need access to the parent client's state, so they're
    created as closures that capture the client reference.
    """

    @staticmethod
    def create_connection_handlers(client) -> Dict[str, Callable]:
        """
        Create connection-related event handlers.

        Args:
            client: OGBWebSocketConManager instance

        Returns:
            Dict mapping event names to handler functions
        """

        async def on_connect():
            _LOGGER.debug(
                f"🔗 {client.ws_room} WebSocket connected, waiting for authentication..."
            )
            client.ws_connected = True

        async def on_disconnect():
            _LOGGER.warning(f"💔 {client.ws_room} WebSocket disconnected")
            await client._handle_connection_loss("disconnect_event")

        async def on_connect_error(data):
            _LOGGER.error(f"❌ Connection error: {data}")
            await client._handle_connection_loss()

        async def on_error(data):
            _LOGGER.error(f"❌ Socket error: {data}")

        return {
            "connect": on_connect,
            "disconnect": on_disconnect,
            "connect_error": on_connect_error,
            "error": on_error,
        }

    @staticmethod
    def create_auth_handlers(client) -> Dict[str, Callable]:
        """
        Create authentication-related event handlers.

        Args:
            client: OGBWebSocketConManager instance

        Returns:
            Dict mapping event names to handler functions
        """

        async def on_auth_success(data):
            _LOGGER.debug(f"✅ Authentication successful: {data}")
            client.authenticated = True
            client.ogb_max_sessions = data.get("ogb_max_sessions")
            client.ogb_sessions = data.get("ogb_sessions")

        async def on_auth_failed(data):
            _LOGGER.error(f"❌ Authentication failed: {data}")
            client.authenticated = False

        return {
            "auth_success": on_auth_success,
            "auth_failed": on_auth_failed,
        }

    @staticmethod
    def create_keepalive_handlers(client) -> Dict[str, Callable]:
        """
        Create keepalive/ping-pong event handlers.

        Args:
            client: OGBWebSocketConManager instance

        Returns:
            Dict mapping event names to handler functions
        """

        async def on_pong(data):
            """Handle pong response with event signaling"""
            client._last_pong_time = time.time()
            client._pong_received = True
            client._pong_event.set()
            client.ogb_sessions = data.get("ogb_sessions")
            _LOGGER.debug(f"🏓 {client.ws_room} Received pong: {data}")

        return {
            "pong": on_pong,
        }

    @staticmethod
    def create_subscription_handlers(client) -> Dict[str, Callable]:
        """
        Create subscription-related event handlers.

        Args:
            client: OGBWebSocketConManager instance

        Returns:
            Dict mapping event names to handler functions
        """

        async def on_subscription_changed(data):
            """Handle subscription changes from Stripe webhooks"""
            try:
                event_type = data.get("event_type", "unknown")
                new_plan = data.get("plan_name", "free")

                _LOGGER.info(f"📡 Subscription changed via {event_type}: {new_plan}")

                old_status = client.is_premium
                client.is_premium = new_plan.lower() != "free"

                if old_status != client.is_premium:
                    _LOGGER.warning(
                        f"🔄 Premium status changed: {old_status} → {client.is_premium}"
                    )

            except Exception as e:
                _LOGGER.error(f"❌ Subscription change handler error: {e}")

        async def on_subscription_expiring_soon(data):
            """Handle subscription expiration warnings"""
            try:
                expires_in = data.get("expires_in_seconds", 0)
                _LOGGER.warning(f"⚠️ Subscription expires in {expires_in} seconds")
            except Exception as e:
                _LOGGER.error(f"❌ Subscription warning handler error: {e}")

        async def on_subscription_expired(data):
            """Handle immediate subscription expiration"""
            try:
                _LOGGER.warning(
                    f"⏰ Subscription expired: {data.get('previous_plan')} → free"
                )
                client.is_premium = False
            except Exception as e:
                _LOGGER.error(f"❌ Subscription expiry handler error: {e}")

        return {
            "subscription_changed": on_subscription_changed,
            "subscription_expiring_soon": on_subscription_expiring_soon,
            "subscription_expired": on_subscription_expired,
        }

    @staticmethod
    def create_feature_flag_handlers(client) -> Dict[str, Callable]:
        """
        Create feature flag control event handlers.

        Args:
            client: OGBWebSocketConManager instance

        Returns:
            Dict mapping event names to handler functions
        """

        async def on_premium_feature_change(data):
            """Handle premium feature availability changes"""
            try:
                changed_features = data.get("changed_features", [])
                new_plan = data.get("plan_name", "unknown")
                subscription_data = data.get("subscription_data", {})

                _LOGGER.info(f"🔄 Premium features changed (plan: {new_plan})")

                if subscription_data:
                    client.subscription_data = subscription_data

                if client.ogbevents:
                    await client.ogbevents.emit(
                        "PremiumFeatureChange",
                        {
                            "room": client.ws_room,
                            "plan_name": new_plan,
                            "changed_features": changed_features,
                            "subscription_data": subscription_data,
                        },
                    )

            except Exception as e:
                _LOGGER.error(f"❌ Premium feature change handler error: {e}")

        async def on_feature_flags_updated(data):
            """Handle feature flag updates from admin dashboard"""
            try:
                tenant_id = data.get("tenant_id")
                feature_key = data.get("feature_key")
                enabled = data.get("enabled")
                source = data.get("source", "admin")

                _LOGGER.info(
                    f"🎛️ Feature flag updated: {feature_key}={enabled} (source: {source})"
                )

                if client.ogbevents:
                    await client.ogbevents.emit(
                        "FeatureFlagUpdated",
                        {
                            "room": client.ws_room,
                            "tenant_id": tenant_id,
                            "feature_key": feature_key,
                            "enabled": enabled,
                            "source": source,
                            "timestamp": time.time(),
                        },
                    )

            except Exception as e:
                _LOGGER.error(f"❌ Feature flag update handler error: {e}")

        async def on_kill_switch_activated(data):
            """Handle emergency kill switch activation from server"""
            try:
                feature_key = data.get("feature_key")
                reason = data.get("reason", "No reason provided")
                activated_by = data.get("activated_by", "system")
                timestamp = data.get("timestamp")

                _LOGGER.warning(
                    f"🚨 KILL SWITCH ACTIVATED: {feature_key} - Reason: {reason}"
                )

                if client.ogbevents:
                    await client.ogbevents.emit(
                        "KillSwitchActivated",
                        {
                            "room": client.ws_room,
                            "feature_key": feature_key,
                            "reason": reason,
                            "activated_by": activated_by,
                            "timestamp": timestamp or time.time(),
                        },
                        haEvent=True,
                    )

                    await client.ogbevents.emit(
                        "LogForClient",
                        f"Feature '{feature_key}' has been disabled: {reason}",
                        haEvent=True,
                    )

            except Exception as e:
                _LOGGER.error(f"❌ Kill switch handler error: {e}")

        async def on_feature_config_changed(data):
            """Handle global feature configuration changes (rollout, etc.)"""
            try:
                feature_key = data.get("feature_key")
                config = data.get("config", {})

                _LOGGER.info(f"⚙️ Feature config changed: {feature_key}")

                if client.ogbevents:
                    await client.ogbevents.emit(
                        "FeatureConfigChanged",
                        {
                            "room": client.ws_room,
                            "feature_key": feature_key,
                            "config": config,
                            "timestamp": time.time(),
                        },
                    )

            except Exception as e:
                _LOGGER.error(f"❌ Feature config change handler error: {e}")

        return {
            "premium_feature_change": on_premium_feature_change,
            "feature_flags_updated": on_feature_flags_updated,
            "kill_switch_activated": on_kill_switch_activated,
            "feature_config_changed": on_feature_config_changed,
        }

    @staticmethod
    def create_analytics_handlers(client) -> Dict[str, Callable]:
        """
        Create analytics/compliance/research event handlers.

        Args:
            client: OGBWebSocketConManager instance

        Returns:
            Dict mapping event names to handler functions
        """

        async def on_analytics_update(data):
            """Handle analytics data updates from server"""
            try:
                update_type = data.get("update_type", "unknown")
                room_id = data.get("room_id")

                _LOGGER.info(f"📊 Analytics update [{update_type}] for room: {room_id}")

                if client.ogbevents:
                    await client.ogbevents.emit(
                        "AnalyticsUpdate",
                        {
                            "room": client.ws_room,
                            "update_type": update_type,
                            "data": data,
                        },
                    )

            except Exception as e:
                _LOGGER.error(f"❌ Analytics update handler error: {e}")

        async def on_compliance_alert(data):
            """Handle compliance alerts from server"""
            try:
                alert_type = data.get("alert_type", "unknown")
                severity = data.get("severity", "info")
                message = data.get("message", "Compliance alert received")

                _LOGGER.warning(f"🚨 Compliance Alert [{severity.upper()}]: {message}")

                if client.ogbevents:
                    await client.ogbevents.emit(
                        "ComplianceAlert",
                        {
                            "room": client.ws_room,
                            "alert_type": alert_type,
                            "severity": severity,
                            "message": message,
                            "data": data,
                        },
                    )

            except Exception as e:
                _LOGGER.error(f"❌ Compliance alert handler error: {e}")

        async def on_dataset_update(data):
            """Handle research dataset updates from server"""
            try:
                dataset_id = data.get("dataset_id")
                update_type = data.get("update_type", "unknown")
                dataset_name = data.get("dataset_name", "Unknown")

                _LOGGER.info(
                    f"📊 Dataset Update [{update_type}]: {dataset_name} ({dataset_id})"
                )

                if client.ogbevents:
                    await client.ogbevents.emit(
                        "DatasetUpdate",
                        {
                            "room": client.ws_room,
                            "dataset_id": dataset_id,
                            "update_type": update_type,
                            "dataset_name": dataset_name,
                            "data": data,
                        },
                    )

            except Exception as e:
                _LOGGER.error(f"❌ Dataset update handler error: {e}")

        return {
            "analytics_update": on_analytics_update,
            "compliance_alert": on_compliance_alert,
            "dataset_update": on_dataset_update,
        }

    @staticmethod
    def create_error_handlers(client) -> Dict[str, Callable]:
        """
        Create error event handlers.

        Args:
            client: OGBWebSocketConManager instance

        Returns:
            Dict mapping event names to handler functions
        """

        async def on_message_error(data):
            _LOGGER.error(f"❌ Message error: {data}")

        async def on_to_many_rooms(data):
            _LOGGER.error(f"❌ {client.ws_room} - {data}")
            await client.ogbevents.emit("ui_to_many_rooms_message", data, haEvent=True)

        async def on_ip_violation(data):
            _LOGGER.error(f"❌ {client.ws_room} - IP VIOLATION- {data}")

        async def on_free_plan_no_access(data):
            _LOGGER.error(f"❌ {client.ws_room} - FREE PLAN NO ACCESS- {data}")

        async def on_session_rotation_error(data):
            """Enhanced rotation error handler"""
            _LOGGER.error(
                f"Session rotation error from server for {client.ws_room}: {data}"
            )
            client._rotation_in_progress = False
            client._rotation_start_time = None

            await client.ogbevents.emit(
                "LogForClient",
                f"Session rotation error for {client.ws_room}: {data.get('error', 'Unknown error')}",
                haEvent=True,
            )

        return {
            "message_error": on_message_error,
            "to_many_rooms": on_to_many_rooms,
            "ip_violation": on_ip_violation,
            "free_plan_no_access": on_free_plan_no_access,
            "session_rotation_error": on_session_rotation_error,
        }


def get_all_event_handlers(client) -> Dict[str, Callable]:
    """
    Get all event handlers for a WebSocket client.

    This is a convenience function that combines all handler categories.

    Args:
        client: OGBWebSocketConManager instance

    Returns:
        Dict mapping all event names to handler functions
    """
    handlers = {}

    handlers.update(OGBPremWebSocketEventHandlers.create_connection_handlers(client))
    handlers.update(OGBPremWebSocketEventHandlers.create_auth_handlers(client))
    handlers.update(OGBPremWebSocketEventHandlers.create_keepalive_handlers(client))
    handlers.update(OGBPremWebSocketEventHandlers.create_subscription_handlers(client))
    handlers.update(OGBPremWebSocketEventHandlers.create_feature_flag_handlers(client))
    handlers.update(OGBPremWebSocketEventHandlers.create_analytics_handlers(client))
    handlers.update(OGBPremWebSocketEventHandlers.create_error_handlers(client))

    return handlers
