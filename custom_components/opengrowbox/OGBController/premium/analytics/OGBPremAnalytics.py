"""
OpenGrowBox Premium Analytics Module

Handles analytics data collection, processing, and reporting for premium features.
Provides methods for yield prediction, anomaly detection, performance metrics,
and data quality assessment.
"""

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

_LOGGER = logging.getLogger(__name__)


class OGBPremAnalytics:
    """
    Analytics module for OpenGrowBox premium features.

    Provides analytics capabilities including:
    - Yield prediction algorithms
    - Anomaly detection
    - Performance scoring
    - Data quality metrics
    - Cache management for analytics data
    """

    def __init__(self, api_proxy=None, cache=None):
        """
        Initialize analytics module.

        Args:
            api_proxy: API proxy for backend communication
            cache: Cache instance for data storage
        """
        self.api_proxy = api_proxy
        self.cache = cache

        # Cache keys
        self._yield_cache_key = "analytics_yield_prediction"
        self._anomaly_cache_key = "analytics_anomaly_score"
        self._performance_cache_key = "analytics_performance_score"

        # Cache TTL (24 hours)
        self._cache_ttl = 86400

        # Analytics data storage
        self._analytics_data = {}

    async def get_yield_prediction(
        self, grow_plan_id: str, days_ahead: int = 7, feature_manager=None
    ) -> Dict[str, Any]:
        """
        Get yield prediction for a grow plan.

        Args:
            grow_plan_id: ID of the grow plan
            days_ahead: Days to predict ahead
            feature_manager: Optional feature manager for access control

        Returns:
            Dictionary with prediction data
        """
        # Check feature access
        if feature_manager and not feature_manager.has_feature("advanced_analytics"):
            raise Exception("Advanced analytics feature not available")

        try:
            cache_key = f"{self._yield_cache_key}_{grow_plan_id}_{days_ahead}"

            # Check cache first
            if self.cache:
                cached = await self.cache.get(cache_key)
                if cached:
                    return cached

            # Fetch from API
            if self.api_proxy:
                result = await self.api_proxy.get_analytics_data(
                    "yield_prediction",
                    {"grow_plan_id": grow_plan_id, "days_ahead": days_ahead},
                )

                if result.get("success"):
                    data = result.get("data", {})

                    # Cache the result
                    if self.cache:
                        await self.cache.set(cache_key, data, ttl=self._cache_ttl)

                    return data

            return {
                "predicted_yield": 0,
                "confidence": 0,
                "days_ahead": days_ahead,
                "error": "No data available",
            }

        except Exception as e:
            _LOGGER.error(f"Error getting yield prediction: {e}")
            return {
                "predicted_yield": 0,
                "confidence": 0,
                "days_ahead": days_ahead,
                "error": str(e),
            }

    async def get_anomaly_score(self, sensor_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Calculate anomaly score for sensor readings.

        Args:
            sensor_data: Current sensor readings

        Returns:
            Dictionary with anomaly analysis
        """
        try:
            cache_key = f"{self._anomaly_cache_key}_{hash(str(sensor_data))}"

            # Check cache
            if self.cache:
                cached = await self.cache.get(cache_key)
                if cached:
                    return cached

            # Simple anomaly detection (placeholder for ML model)
            score = self._calculate_simple_anomaly_score(sensor_data)

            result = {
                "anomaly_score": score,
                "severity": self._classify_anomaly_severity(score),
                "timestamp": datetime.now().isoformat(),
                "sensor_count": len(sensor_data),
            }

            # Cache result
            if self.cache:
                await self.cache.set(cache_key, result, ttl=self._cache_ttl)

            return result

        except Exception as e:
            _LOGGER.error(f"Error calculating anomaly score: {e}")
            return {"anomaly_score": 0.5, "severity": "unknown", "error": str(e)}

    async def get_performance_score(self, grow_plan_id: str) -> Dict[str, Any]:
        """
        Calculate overall performance score for a grow plan.

        Args:
            grow_plan_id: ID of the grow plan

        Returns:
            Dictionary with performance metrics
        """
        try:
            cache_key = f"{self._performance_cache_key}_{grow_plan_id}"

            # Check cache
            if self.cache:
                cached = await self.cache.get(cache_key)
                if cached:
                    return cached

            # Calculate performance score (placeholder)
            score = await self._calculate_performance_score(grow_plan_id)

            result = {
                "performance_score": score,
                "grade": self._score_to_grade(score),
                "timestamp": datetime.now().isoformat(),
                "grow_plan_id": grow_plan_id,
            }

            # Cache result
            if self.cache:
                await self.cache.set(cache_key, result, ttl=self._cache_ttl)

            return result

        except Exception as e:
            _LOGGER.error(f"Error calculating performance score: {e}")
            return {"performance_score": 50, "grade": "C", "error": str(e)}

    async def invalidate_cache(self, grow_plan_id: str = None, metric_type: str = None):
        """
        Invalidate analytics cache.

        Args:
            grow_plan_id: Specific grow plan to invalidate (optional)
            metric_type: Specific metric type to invalidate (optional)
        """
        try:
            if self.cache:
                if metric_type and grow_plan_id:
                    # Invalidate specific metric
                    cache_key = f"analytics_{metric_type}_{grow_plan_id}"
                    await self.cache.delete(cache_key)
                    _LOGGER.debug(
                        f"Invalidated cache for {metric_type} on grow plan {grow_plan_id}"
                    )
                elif grow_plan_id:
                    # Invalidate all metrics for grow plan
                    patterns = [
                        f"analytics_*_{grow_plan_id}_*",
                        f"analytics_*_{grow_plan_id}",
                    ]
                    for pattern in patterns:
                        await self.cache.delete_pattern(pattern)
                    _LOGGER.debug(
                        f"Invalidated all analytics cache for grow plan {grow_plan_id}"
                    )
                else:
                    # Invalidate all analytics cache
                    await self.cache.delete_pattern("analytics_*")
                    _LOGGER.debug("Invalidated all analytics cache")

        except Exception as e:
            _LOGGER.error(f"Error invalidating analytics cache: {e}")

    def _calculate_simple_anomaly_score(self, sensor_data: Dict[str, Any]) -> float:
        """
        Simple anomaly detection algorithm.

        Args:
            sensor_data: Sensor readings

        Returns:
            Anomaly score between 0-1
        """
        # Placeholder implementation
        # In a real system, this would use statistical methods or ML

        score = 0.0
        checks = 0

        # Check temperature
        if "temperature" in sensor_data:
            temp = sensor_data["temperature"]
            if not (15 <= temp <= 35):  # Reasonable grow temp range
                score += 0.3
            checks += 1

        # Check humidity
        if "humidity" in sensor_data:
            humidity = sensor_data["humidity"]
            if not (30 <= humidity <= 80):  # Reasonable humidity range
                score += 0.2
            checks += 1

        # Check VPD
        if "vpd" in sensor_data:
            vpd = sensor_data["vpd"]
            if not (0.4 <= vpd <= 2.0):  # Reasonable VPD range
                score += 0.4
            checks += 1

        # Check pH
        if "ph" in sensor_data:
            ph = sensor_data["ph"]
            if not (5.5 <= ph <= 7.0):  # Reasonable pH range
                score += 0.3
            checks += 1

        # Check EC
        if "ec" in sensor_data:
            ec = sensor_data["ec"]
            if not (0.8 <= ec <= 2.5):  # Reasonable EC range
                score += 0.2
            checks += 1

        return min(score / max(checks, 1), 1.0)

    def _classify_anomaly_severity(self, score: float) -> str:
        """
        Classify anomaly severity based on score.

        Args:
            score: Anomaly score (0-1)

        Returns:
            Severity classification
        """
        if score < 0.2:
            return "normal"
        elif score < 0.4:
            return "low"
        elif score < 0.6:
            return "medium"
        elif score < 0.8:
            return "high"
        else:
            return "critical"

    async def _calculate_performance_score(self, grow_plan_id: str) -> float:
        """
        Calculate performance score for a grow plan.

        Args:
            grow_plan_id: Grow plan ID

        Returns:
            Performance score (0-100)
        """
        # Placeholder implementation
        # In a real system, this would analyze historical data

        # Simulate some analysis
        await asyncio.sleep(0.01)  # Simulate processing time

        # Return a random-ish score based on grow plan ID hash
        import hashlib

        hash_val = int(hashlib.md5(grow_plan_id.encode()).hexdigest()[:8], 16)
        return (hash_val % 60) + 20  # Score between 20-80

    def _score_to_grade(self, score: float) -> str:
        """
        Convert numeric score to letter grade.

        Args:
            score: Numeric score (0-100)

        Returns:
            Letter grade
        """
        if score >= 90:
            return "A"
        elif score >= 80:
            return "B"
        elif score >= 70:
            return "C"
        elif score >= 60:
            return "D"
        else:
            return "F"
