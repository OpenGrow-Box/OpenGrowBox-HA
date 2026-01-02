"""
╔══════════════════════════════════════════════════════════════════════════════╗
║                         🔒 PREMIUM FEATURE FILE 🔒                          ║
║                   Local SQLite Cache for Premium Features                    ║
╚══════════════════════════════════════════════════════════════════════════════╝

⚠️  IMPORTANT: This file provides caching for PREMIUM features.

OGBCache - Local SQLite Cache for Premium Features

Provides local caching for analytics, compliance, and research data to reduce
API calls and improve response times.

Cache Strategy:
- Analytics: 5 minute TTL (data changes frequently)
- Compliance: 1 hour TTL (rules change infrequently)
- Research: 30 minute TTL (moderate change rate)

Database Location: .storage/opengrowbox/{room}_cache.db
Background Cleanup: Every 15 minutes
"""

import asyncio
import json
import logging
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

_LOGGER = logging.getLogger(__name__)


class OGBCache:
    """Local SQLite cache for premium feature data"""

    # TTL settings (in seconds)
    TTL_ANALYTICS = 300  # 5 minutes
    TTL_COMPLIANCE = 3600  # 1 hour
    TTL_RESEARCH = 1800  # 30 minutes

    def __init__(self, hass, room_name: str):
        """
        Initialize cache database.

        Args:
            hass: Home Assistant instance
            room_name: Room name for cache isolation
        """
        self.hass = hass
        self.room_name = room_name

        # Database path: .storage/opengrowbox/{room}_cache.db
        storage_path = Path(hass.config.path(".storage", "opengrowbox"))
        storage_path.mkdir(parents=True, exist_ok=True)

        self.db_path = storage_path / f"{room_name}_cache.db"
        self.connection = None

        # Background tasks
        self._cleanup_task = None

        _LOGGER.info(f"OGBCache initialized for {room_name} at {self.db_path}")

    async def initialize(self):
        """Initialize database connection and create tables."""
        try:
            # Run in executor to avoid blocking
            await self.hass.async_add_executor_job(self._create_tables)

            # Start background cleanup task (runs every 15 minutes)
            self._cleanup_task = asyncio.create_task(self._periodic_cleanup())

            _LOGGER.info(f"✅ {self.room_name} Cache database initialized")
        except Exception as e:
            _LOGGER.error(f"Failed to initialize cache database: {e}", exc_info=True)

    def _create_tables(self):
        """Create cache tables if they don't exist."""
        conn = sqlite3.connect(str(self.db_path))
        cursor = conn.cursor()

        # Analytics cache table
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS analytics_cache (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                grow_plan_id TEXT,
                metric_type TEXT,
                value TEXT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                expires_at DATETIME,
                UNIQUE(grow_plan_id, metric_type)
            )
        """
        )

        # Compliance cache table
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS compliance_cache (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                rule_id TEXT UNIQUE,
                status TEXT,
                last_validated DATETIME,
                expires_at DATETIME,
                data TEXT
            )
        """
        )

        # Research datasets cache
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS research_datasets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                dataset_id TEXT UNIQUE,
                metadata TEXT,
                cached_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                expires_at DATETIME
            )
        """
        )

        # Subscription tiers table
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS subscription_tiers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tier_name TEXT UNIQUE,
                room_limit INTEGER,
                price_monthly REAL,
                features TEXT,
                description TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """
        )

        # User subscriptions table
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS user_subscriptions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT UNIQUE,
                tier_name TEXT,
                room_count INTEGER DEFAULT 0,
                subscription_start DATETIME,
                subscription_end DATETIME,
                status TEXT DEFAULT 'active',
                last_updated DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (tier_name) REFERENCES subscription_tiers(tier_name)
            )
        """
        )

        # Initialize tier data if empty
        cursor.execute("SELECT COUNT(*) FROM subscription_tiers")
        if cursor.fetchone()[0] == 0:
            tiers_data = [
                (
                    "free",
                    1,
                    0.0,
                    '["basic_monitoring", "ai_controllers", "mobile_app"]',
                    "Basic monitoring only",
                ),
                (
                    "starter",
                    2,
                    9.0,
                    '["basic_monitoring", "ai_controllers", "mobile_app", "advanced_analytics", "notifications", "data_export", "api_access"]',
                    "Analytics features added",
                ),
                (
                    "grower",
                    5,
                    29.0,
                    '["basic_monitoring", "ai_controllers", "mobile_app", "advanced_analytics", "notifications", "data_export", "api_access", "basic_compliance", "audit_logs"]',
                    "Basic compliance features",
                ),
                (
                    "professional",
                    15,
                    79.0,
                    '["basic_monitoring", "ai_controllers", "mobile_app", "advanced_analytics", "notifications", "data_export", "api_access", "basic_compliance", "advanced_compliance", "research_data", "audit_logs", "multi_tenant"]',
                    "Full compliance + research data",
                ),
                (
                    "enterprise",
                    999999,
                    199.0,
                    '["basic_monitoring", "ai_controllers", "mobile_app", "advanced_analytics", "notifications", "data_export", "api_access", "basic_compliance", "advanced_compliance", "research_data", "audit_logs", "multi_tenant", "webhooks", "priority_support", "custom_integrations", "data_marketplace"]',
                    "Everything + priority support",
                ),
            ]

            cursor.executemany(
                """
                INSERT INTO subscription_tiers (tier_name, room_limit, price_monthly, features, description)
                VALUES (?, ?, ?, ?, ?)
            """,
                tiers_data,
            )

        # Create indexes for faster lookups
        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_analytics_grow_plan 
            ON analytics_cache(grow_plan_id)
        """
        )
        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_analytics_expires 
            ON analytics_cache(expires_at)
        """
        )
        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_compliance_expires 
            ON compliance_cache(expires_at)
        """
        )
        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_research_expires 
            ON research_datasets(expires_at)
        """
        )
        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_user_subscriptions_user_id 
            ON user_subscriptions(user_id)
        """
        )
        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_user_subscriptions_tier 
            ON user_subscriptions(tier_name)
        """
        )

        conn.commit()
        conn.close()

    async def get_analytics(
        self, grow_plan_id: str, metric_type: str
    ) -> Optional[Dict[str, Any]]:
        """
        Get cached analytics data.

        Args:
            grow_plan_id: Grow plan ID
            metric_type: Type of metric (yield_prediction, insights, etc.)

        Returns:
            Cached data dict or None if not found/expired
        """

        def _get():
            conn = sqlite3.connect(str(self.db_path))
            cursor = conn.cursor()

            cursor.execute(
                """
                SELECT value, expires_at
                FROM analytics_cache
                WHERE grow_plan_id = ? AND metric_type = ?
            """,
                (grow_plan_id, metric_type),
            )

            row = cursor.fetchone()
            conn.close()

            if not row:
                return None

            value_json, expires_at = row

            # Check if expired
            expires_dt = datetime.fromisoformat(expires_at)
            if datetime.now() > expires_dt:
                _LOGGER.debug(f"Cache expired for {grow_plan_id}/{metric_type}")
                return None

            # Parse and return
            try:
                data = json.loads(value_json)
                _LOGGER.debug(f"✅ Cache HIT: {grow_plan_id}/{metric_type}")
                return data
            except json.JSONDecodeError:
                _LOGGER.error(f"Invalid JSON in cache for {grow_plan_id}/{metric_type}")
                return None

        return await self.hass.async_add_executor_job(_get)

    async def set_analytics(
        self,
        grow_plan_id: str,
        metric_type: str,
        value: Dict[str, Any],
        ttl: Optional[int] = None,
    ):
        """
        Store analytics data in cache.

        Args:
            grow_plan_id: Grow plan ID
            metric_type: Type of metric
            value: Data to cache
            ttl: Time to live in seconds (default: TTL_ANALYTICS)
        """
        ttl = ttl or self.TTL_ANALYTICS
        expires_at = datetime.now() + timedelta(seconds=ttl)
        value_json = json.dumps(value)

        def _set():
            conn = sqlite3.connect(str(self.db_path))
            cursor = conn.cursor()

            cursor.execute(
                """
                INSERT OR REPLACE INTO analytics_cache 
                (grow_plan_id, metric_type, value, expires_at)
                VALUES (?, ?, ?, ?)
            """,
                (grow_plan_id, metric_type, value_json, expires_at.isoformat()),
            )

            conn.commit()
            conn.close()

            _LOGGER.debug(f"✅ Cached analytics: {grow_plan_id}/{metric_type}")

        await self.hass.async_add_executor_job(_set)

    async def get_compliance(self, rule_id: str) -> Optional[Dict[str, Any]]:
        """
        Get cached compliance rule.

        Args:
            rule_id: Compliance rule ID

        Returns:
            Cached rule data or None
        """

        def _get():
            conn = sqlite3.connect(str(self.db_path))
            cursor = conn.cursor()

            cursor.execute(
                """
                SELECT status, data, expires_at
                FROM compliance_cache
                WHERE rule_id = ?
            """,
                (rule_id,),
            )

            row = cursor.fetchone()
            conn.close()

            if not row:
                return None

            status, data_json, expires_at = row

            # Check if expired
            expires_dt = datetime.fromisoformat(expires_at)
            if datetime.now() > expires_dt:
                return None

            try:
                data = json.loads(data_json) if data_json else {}
                result = {"status": status, **data}
                _LOGGER.debug(f"✅ Cache HIT: compliance/{rule_id}")
                return result
            except json.JSONDecodeError:
                return None

        return await self.hass.async_add_executor_job(_get)

    async def set_compliance(
        self,
        rule_id: str,
        status: str,
        data: Optional[Dict[str, Any]] = None,
        ttl: Optional[int] = None,
    ):
        """
        Store compliance rule in cache.

        Args:
            rule_id: Compliance rule ID
            status: Rule status
            data: Additional rule data
            ttl: Time to live in seconds (default: TTL_COMPLIANCE)
        """
        ttl = ttl or self.TTL_COMPLIANCE
        expires_at = datetime.now() + timedelta(seconds=ttl)
        data_json = json.dumps(data) if data else None

        def _set():
            conn = sqlite3.connect(str(self.db_path))
            cursor = conn.cursor()

            cursor.execute(
                """
                INSERT OR REPLACE INTO compliance_cache 
                (rule_id, status, last_validated, data, expires_at)
                VALUES (?, ?, ?, ?, ?)
            """,
                (
                    rule_id,
                    status,
                    datetime.now().isoformat(),
                    data_json,
                    expires_at.isoformat(),
                ),
            )

            conn.commit()
            conn.close()

            _LOGGER.debug(f"✅ Cached compliance: {rule_id}")

        await self.hass.async_add_executor_job(_set)

    async def get_dataset(self, dataset_id: str) -> Optional[Dict[str, Any]]:
        """
        Get cached research dataset metadata.

        Args:
            dataset_id: Dataset ID

        Returns:
            Cached dataset metadata or None
        """

        def _get():
            conn = sqlite3.connect(str(self.db_path))
            cursor = conn.cursor()

            cursor.execute(
                """
                SELECT metadata, expires_at
                FROM research_datasets
                WHERE dataset_id = ?
            """,
                (dataset_id,),
            )

            row = cursor.fetchone()
            conn.close()

            if not row:
                return None

            metadata_json, expires_at = row

            # Check if expired
            expires_dt = datetime.fromisoformat(expires_at)
            if datetime.now() > expires_dt:
                return None

            try:
                metadata = json.loads(metadata_json)
                _LOGGER.debug(f"✅ Cache HIT: dataset/{dataset_id}")
                return metadata
            except json.JSONDecodeError:
                return None

        return await self.hass.async_add_executor_job(_get)

    async def set_dataset(
        self, dataset_id: str, metadata: Dict[str, Any], ttl: Optional[int] = None
    ):
        """
        Store research dataset metadata in cache.

        Args:
            dataset_id: Dataset ID
            metadata: Dataset metadata
            ttl: Time to live in seconds (default: TTL_RESEARCH)
        """
        ttl = ttl or self.TTL_RESEARCH
        expires_at = datetime.now() + timedelta(seconds=ttl)
        metadata_json = json.dumps(metadata)

        def _set():
            conn = sqlite3.connect(str(self.db_path))
            cursor = conn.cursor()

            cursor.execute(
                """
                INSERT OR REPLACE INTO research_datasets 
                (dataset_id, metadata, expires_at)
                VALUES (?, ?, ?)
            """,
                (dataset_id, metadata_json, expires_at.isoformat()),
            )

            conn.commit()
            conn.close()

            _LOGGER.debug(f"✅ Cached dataset: {dataset_id}")

        await self.hass.async_add_executor_job(_set)

    async def invalidate_analytics(
        self, grow_plan_id: Optional[str] = None, metric_type: Optional[str] = None
    ):
        """
        Invalidate analytics cache.

        Args:
            grow_plan_id: Specific grow plan to invalidate (None = all)
            metric_type: Specific metric type to invalidate (None = all)
        """

        def _invalidate():
            conn = sqlite3.connect(str(self.db_path))
            cursor = conn.cursor()

            if grow_plan_id and metric_type:
                cursor.execute(
                    """
                    DELETE FROM analytics_cache
                    WHERE grow_plan_id = ? AND metric_type = ?
                """,
                    (grow_plan_id, metric_type),
                )
            elif grow_plan_id:
                cursor.execute(
                    """
                    DELETE FROM analytics_cache
                    WHERE grow_plan_id = ?
                """,
                    (grow_plan_id,),
                )
            else:
                cursor.execute("DELETE FROM analytics_cache")

            deleted = cursor.rowcount
            conn.commit()
            conn.close()

            _LOGGER.info(f"🗑️ Invalidated {deleted} analytics cache entries")

        await self.hass.async_add_executor_job(_invalidate)

    async def invalidate_compliance(self, rule_id: Optional[str] = None):
        """
        Invalidate compliance cache.

        Args:
            rule_id: Specific rule to invalidate (None = all)
        """

        def _invalidate():
            conn = sqlite3.connect(str(self.db_path))
            cursor = conn.cursor()

            if rule_id:
                cursor.execute(
                    """
                    DELETE FROM compliance_cache WHERE rule_id = ?
                """,
                    (rule_id,),
                )
            else:
                cursor.execute("DELETE FROM compliance_cache")

            deleted = cursor.rowcount
            conn.commit()
            conn.close()

            _LOGGER.info(f"🗑️ Invalidated {deleted} compliance cache entries")

        await self.hass.async_add_executor_job(_invalidate)

    async def invalidate_datasets(self, dataset_id: Optional[str] = None):
        """
        Invalidate research datasets cache.

        Args:
            dataset_id: Specific dataset to invalidate (None = all)
        """

        def _invalidate():
            conn = sqlite3.connect(str(self.db_path))
            cursor = conn.cursor()

            if dataset_id:
                cursor.execute(
                    """
                    DELETE FROM research_datasets WHERE dataset_id = ?
                """,
                    (dataset_id,),
                )
            else:
                cursor.execute("DELETE FROM research_datasets")

            deleted = cursor.rowcount
            conn.commit()
            conn.close()

            _LOGGER.info(f"🗑️ Invalidated {deleted} dataset cache entries")

        await self.hass.async_add_executor_job(_invalidate)

    async def clear_all(self):
        """Clear all cached data."""

        def _clear():
            conn = sqlite3.connect(str(self.db_path))
            cursor = conn.cursor()

            cursor.execute("DELETE FROM analytics_cache")
            cursor.execute("DELETE FROM compliance_cache")
            cursor.execute("DELETE FROM research_datasets")

            conn.commit()
            conn.close()

            _LOGGER.info(f"🗑️ {self.room_name} All cache cleared")

        await self.hass.async_add_executor_job(_clear)

    async def get_stats(self) -> Dict[str, Any]:
        """
        Get cache statistics.

        Returns:
            Dict with cache stats (counts, sizes, hit rates)
        """

        def _stats():
            conn = sqlite3.connect(str(self.db_path))
            cursor = conn.cursor()

            # Count entries
            cursor.execute("SELECT COUNT(*) FROM analytics_cache")
            analytics_count = cursor.fetchone()[0]

            cursor.execute("SELECT COUNT(*) FROM compliance_cache")
            compliance_count = cursor.fetchone()[0]

            cursor.execute("SELECT COUNT(*) FROM research_datasets")
            datasets_count = cursor.fetchone()[0]

            # Count expired entries
            now = datetime.now().isoformat()

            cursor.execute(
                """
                SELECT COUNT(*) FROM analytics_cache WHERE expires_at < ?
            """,
                (now,),
            )
            analytics_expired = cursor.fetchone()[0]

            cursor.execute(
                """
                SELECT COUNT(*) FROM compliance_cache WHERE expires_at < ?
            """,
                (now,),
            )
            compliance_expired = cursor.fetchone()[0]

            cursor.execute(
                """
                SELECT COUNT(*) FROM research_datasets WHERE expires_at < ?
            """,
                (now,),
            )
            datasets_expired = cursor.fetchone()[0]

            conn.close()

            return {
                "analytics": {
                    "total": analytics_count,
                    "expired": analytics_expired,
                    "valid": analytics_count - analytics_expired,
                },
                "compliance": {
                    "total": compliance_count,
                    "expired": compliance_expired,
                    "valid": compliance_count - compliance_expired,
                },
                "datasets": {
                    "total": datasets_count,
                    "expired": datasets_expired,
                    "valid": datasets_count - datasets_expired,
                },
                "database_path": str(self.db_path),
            }

        return await self.hass.async_add_executor_job(_stats)

    async def _periodic_cleanup(self):
        """Background task to clean up expired cache entries."""
        while True:
            try:
                await asyncio.sleep(900)  # 15 minutes
                await self._cleanup_expired()
            except asyncio.CancelledError:
                break
            except Exception as e:
                _LOGGER.error(f"Cache cleanup error: {e}")

    async def _cleanup_expired(self):
        """Remove expired cache entries."""

        def _cleanup():
            conn = sqlite3.connect(str(self.db_path))
            cursor = conn.cursor()
            now = datetime.now().isoformat()

            cursor.execute(
                """
                DELETE FROM analytics_cache WHERE expires_at < ?
            """,
                (now,),
            )
            analytics_deleted = cursor.rowcount

            cursor.execute(
                """
                DELETE FROM compliance_cache WHERE expires_at < ?
            """,
                (now,),
            )
            compliance_deleted = cursor.rowcount

            cursor.execute(
                """
                DELETE FROM research_datasets WHERE expires_at < ?
            """,
                (now,),
            )
            datasets_deleted = cursor.rowcount

            conn.commit()
            conn.close()

            total = analytics_deleted + compliance_deleted + datasets_deleted
            if total > 0:
                _LOGGER.info(
                    f"🧹 {self.room_name} Cleaned {total} expired cache entries "
                    f"(analytics: {analytics_deleted}, compliance: {compliance_deleted}, "
                    f"datasets: {datasets_deleted})"
                )

        await self.hass.async_add_executor_job(_cleanup)

    # === Subscription Tier Management ===

    async def get_user_subscription(self, user_id: str) -> Optional[Dict[str, Any]]:
        """Get user's current subscription details."""

        def _get():
            conn = sqlite3.connect(str(self.db_path))
            cursor = conn.cursor()

            cursor.execute(
                """
                SELECT us.tier_name, us.room_count, us.status, us.subscription_start, us.subscription_end,
                       st.room_limit, st.price_monthly, st.features, st.description
                FROM user_subscriptions us
                JOIN subscription_tiers st ON us.tier_name = st.tier_name
                WHERE us.user_id = ? AND us.status = 'active'
            """,
                (user_id,),
            )

            row = cursor.fetchone()
            conn.close()

            if not row:
                # Return free tier as default
                return {
                    "tier_name": "free",
                    "room_count": 0,
                    "status": "active",
                    "room_limit": 1,
                    "price_monthly": 0.0,
                    "features": ["basic_monitoring", "ai_controllers", "mobile_app"],
                    "description": "Basic monitoring only",
                }

            return {
                "tier_name": row[0],
                "room_count": row[1],
                "status": row[2],
                "subscription_start": row[3],
                "subscription_end": row[4],
                "room_limit": row[5],
                "price_monthly": row[6],
                "features": json.loads(row[7]),
                "description": row[8],
            }

        return await self.hass.async_add_executor_job(_get)

    async def update_user_subscription(
        self, user_id: str, tier_name: str, room_count: int = 0
    ) -> bool:
        """Update or create user subscription."""

        def _update():
            conn = sqlite3.connect(str(self.db_path))
            cursor = conn.cursor()

            # Upsert user subscription
            cursor.execute(
                """
                INSERT OR REPLACE INTO user_subscriptions 
                (user_id, tier_name, room_count, subscription_start, status, last_updated)
                VALUES (?, ?, ?, CURRENT_TIMESTAMP, 'active', CURRENT_TIMESTAMP)
            """,
                (user_id, tier_name, room_count),
            )

            conn.commit()
            success = cursor.rowcount > 0
            conn.close()
            return success

        result = await self.hass.async_add_executor_job(_update)

        if result:
            _LOGGER.info(
                f"💳 {self.room_name} Updated subscription: {user_id[:6]} → {tier_name} ({room_count} rooms)"
            )

        return result

    async def increment_room_count(self, user_id: str) -> bool:
        """Increment user's room count (when creating a new room)."""

        def _increment():
            conn = sqlite3.connect(str(self.db_path))
            cursor = conn.cursor()

            cursor.execute(
                """
                UPDATE user_subscriptions 
                SET room_count = room_count + 1, last_updated = CURRENT_TIMESTAMP
                WHERE user_id = ? AND status = 'active'
            """,
                (user_id,),
            )

            conn.commit()
            success = cursor.rowcount > 0
            conn.close()
            return success

        return await self.hass.async_add_executor_job(_increment)

    async def get_tier_info(self, tier_name: str) -> Optional[Dict[str, Any]]:
        """Get tier information."""

        def _get():
            conn = sqlite3.connect(str(self.db_path))
            cursor = conn.cursor()

            cursor.execute(
                """
                SELECT tier_name, room_limit, price_monthly, features, description
                FROM subscription_tiers
                WHERE tier_name = ?
            """,
                (tier_name,),
            )

            row = cursor.fetchone()
            conn.close()

            if not row:
                return None

            return {
                "tier_name": row[0],
                "room_limit": row[1],
                "price_monthly": row[2],
                "features": json.loads(row[3]),
                "description": row[4],
            }

        return await self.hass.async_add_executor_job(_get)

    async def get_all_tiers(self) -> List[Dict[str, Any]]:
        """Get all available subscription tiers."""

        def _get():
            conn = sqlite3.connect(str(self.db_path))
            cursor = conn.cursor()

            cursor.execute(
                """
                SELECT tier_name, room_limit, price_monthly, features, description
                FROM subscription_tiers
                ORDER BY price_monthly ASC
            """
            )

            rows = cursor.fetchall()
            conn.close()

            tiers = []
            for row in rows:
                tiers.append(
                    {
                        "tier_name": row[0],
                        "room_limit": row[1],
                        "price_monthly": row[2],
                        "features": json.loads(row[3]),
                        "description": row[4],
                    }
                )

            return tiers

        return await self.hass.async_add_executor_job(_get)

    async def shutdown(self):
        """Shutdown cache and cleanup resources."""
        if self._cleanup_task:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass

        _LOGGER.info(f"{self.room_name} Cache shutdown complete")
