"""
OpenGrowBox Premium Compliance Module

Handles regulatory compliance checking, violation tracking, and reporting.
Provides methods for compliance status monitoring, alert generation,
and compliance documentation.
"""

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

_LOGGER = logging.getLogger(__name__)


class OGBPremCompliance:
    """
    Compliance module for OpenGrowBox premium features.

    Provides compliance capabilities including:
    - Regulatory compliance checking
    - Violation tracking and reporting
    - Compliance status monitoring
    - Alert generation for violations
    - Compliance documentation
    """

    def __init__(self, api_proxy=None, cache=None):
        """
        Initialize compliance module.

        Args:
            api_proxy: API proxy for backend communication
            cache: Cache instance for data storage
        """
        self.api_proxy = api_proxy
        self.cache = cache

        # Cache keys
        self._compliance_status_key = "compliance_status"
        self._violations_key = "compliance_violations"

        # Cache TTL (1 hour for compliance data)
        self._cache_ttl = 3600

        # Compliance data storage
        self._compliance_data = {}
        self._violations = []

    async def get_compliance_status(self) -> Dict[str, Any]:
        """
        Get current compliance status.

        Returns:
            Dictionary with compliance status information
        """
        try:
            # Check cache first
            if self.cache:
                cached = await self.cache.get(self._compliance_status_key)
                if cached:
                    return cached

            # Fetch from API
            if self.api_proxy:
                result = await self.api_proxy.get_compliance_data("status")

                if result.get("success"):
                    data = result.get("data", {})

                    # Cache the result
                    if self.cache:
                        await self.cache.set(
                            self._compliance_status_key, data, ttl=self._cache_ttl
                        )

                    return data

            return {
                "overall_status": "unknown",
                "violations_count": 0,
                "last_check": datetime.now().isoformat(),
                "error": "No data available",
            }

        except Exception as e:
            _LOGGER.error(f"Error getting compliance status: {e}")
            return {
                "overall_status": "error",
                "violations_count": 0,
                "last_check": datetime.now().isoformat(),
                "error": str(e),
            }

    async def get_violations_count(self) -> int:
        """
        Get count of current violations.

        Returns:
            Number of active violations
        """
        try:
            status = await self.get_compliance_status()
            return status.get("violations_count", 0)

        except Exception as e:
            _LOGGER.error(f"Error getting violations count: {e}")
            return 0

    async def check_compliance(self, sensor_data: Dict[str, Any], feature_manager=None) -> Dict[str, Any]:
        """
        Check compliance for current sensor readings.

        Args:
            sensor_data: Current sensor readings
            feature_manager: Optional feature manager for access control

        Returns:
            Dictionary with compliance check results
        """
        # Check feature access
        if feature_manager and not feature_manager.has_feature("compliance"):
            raise Exception("Compliance feature not available")

        try:
            violations = []

            # Check temperature compliance (example regulation)
            if "temperature" in sensor_data:
                temp = sensor_data["temperature"]
                if temp > 30:  # Example: max temp regulation
                    violations.append(
                        {
                            "type": "temperature",
                            "severity": "warning",
                            "value": temp,
                            "limit": 30,
                            "message": f"Temperature {temp}°C exceeds regulatory limit of 30°C",
                        }
                    )
                elif temp < 15:  # Example: min temp regulation
                    violations.append(
                        {
                            "type": "temperature",
                            "severity": "critical",
                            "value": temp,
                            "limit": 15,
                            "message": f"Temperature {temp}°C below regulatory minimum of 15°C",
                        }
                    )

            # Check humidity compliance
            if "humidity" in sensor_data:
                humidity = sensor_data["humidity"]
                if humidity > 80:  # Example: max humidity regulation
                    violations.append(
                        {
                            "type": "humidity",
                            "severity": "warning",
                            "value": humidity,
                            "limit": 80,
                            "message": f"Humidity {humidity}% exceeds regulatory limit of 80%",
                        }
                    )

            # Check VPD compliance
            if "vpd" in sensor_data:
                vpd = sensor_data["vpd"]
                if vpd > 1.6:  # Example: max VPD regulation
                    violations.append(
                        {
                            "type": "vpd",
                            "severity": "critical",
                            "value": vpd,
                            "limit": 1.6,
                            "message": f"VPD {vpd} kPa exceeds regulatory limit of 1.6 kPa",
                        }
                    )

            # Determine overall status
            if any(v["severity"] == "critical" for v in violations):
                overall_status = "critical"
            elif any(v["severity"] == "warning" for v in violations):
                overall_status = "warning"
            elif violations:
                overall_status = "minor"
            else:
                overall_status = "compliant"

            return {
                "overall_status": overall_status,
                "violations_count": len(violations),
                "violations": violations,
                "timestamp": datetime.now().isoformat(),
                "checked_sensors": list(sensor_data.keys()),
            }

        except Exception as e:
            _LOGGER.error(f"Error checking compliance: {e}")
            return {
                "overall_status": "error",
                "violations_count": 0,
                "violations": [],
                "error": str(e),
            }

    async def handle_compliance_alert(self, alert_data: Dict[str, Any]):
        """
        Handle compliance alert from WebSocket or API.

        Args:
            alert_data: Alert data from the system
        """
        try:
            alert_type = alert_data.get("alert_type", "unknown")
            severity = alert_data.get("severity", "info")
            message = alert_data.get("message", "Compliance alert received")

            _LOGGER.warning(f"🚨 Compliance Alert [{severity.upper()}]: {message}")

            # Invalidate compliance cache when alert received
            if self.cache:
                await self.cache.delete(self._compliance_status_key)
                await self.cache.delete(self._violations_key)

            # Store alert for tracking
            alert_record = {
                "type": alert_type,
                "severity": severity,
                "message": message,
                "timestamp": datetime.now().isoformat(),
                "data": alert_data,
            }

            self._violations.append(alert_record)

            # Keep only recent violations (last 100)
            if len(self._violations) > 100:
                self._violations = self._violations[-100:]

        except Exception as e:
            _LOGGER.error(f"Error handling compliance alert: {e}")

    async def get_compliance_report(self, days: int = 7) -> Dict[str, Any]:
        """
        Generate compliance report for the specified period.

        Args:
            days: Number of days to include in report

        Returns:
            Dictionary with compliance report
        """
        try:
            cutoff_date = datetime.now() - timedelta(days=days)

            # Filter violations within the time period
            recent_violations = []
            for violation in self._violations:
                try:
                    violation_time = datetime.fromisoformat(violation["timestamp"])
                    if violation_time >= cutoff_date:
                        recent_violations.append(violation)
                except (ValueError, KeyError):
                    continue

            # Calculate statistics
            total_violations = len(recent_violations)
            critical_count = sum(
                1 for v in recent_violations if v.get("severity") == "critical"
            )
            warning_count = sum(
                1 for v in recent_violations if v.get("severity") == "warning"
            )

            # Group by type
            violations_by_type = {}
            for violation in recent_violations:
                v_type = violation.get("type", "unknown")
                if v_type not in violations_by_type:
                    violations_by_type[v_type] = []
                violations_by_type[v_type].append(violation)

            return {
                "report_period_days": days,
                "total_violations": total_violations,
                "critical_violations": critical_count,
                "warning_violations": warning_count,
                "violations_by_type": violations_by_type,
                "generated_at": datetime.now().isoformat(),
                "compliance_score": self._calculate_compliance_score(
                    total_violations, critical_count, warning_count
                ),
            }

        except Exception as e:
            _LOGGER.error(f"Error generating compliance report: {e}")
            return {
                "error": str(e),
                "report_period_days": days,
                "generated_at": datetime.now().isoformat(),
            }

    def _calculate_compliance_score(
        self, total_violations: int, critical: int, warning: int
    ) -> float:
        """
        Calculate compliance score based on violations.

        Args:
            total_violations: Total number of violations
            critical: Number of critical violations
            warning: Number of warning violations

        Returns:
            Compliance score (0-100)
        """
        # Base score of 100
        score = 100.0

        # Deduct for critical violations (5 points each)
        score -= critical * 5

        # Deduct for warning violations (2 points each)
        score -= warning * 2

        # Deduct for total violations (1 point each)
        score -= total_violations * 1

        # Ensure score doesn't go below 0
        return max(0.0, score)
