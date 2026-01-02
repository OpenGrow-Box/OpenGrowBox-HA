"""
╔══════════════════════════════════════════════════════════════════════════════╗
║                         🔒 PREMIUM FEATURE FILE 🔒                          ║
║                          Premium Sensor Entities                             ║
╚══════════════════════════════════════════════════════════════════════════════╝

⚠️  IMPORTANT: This file contains PREMIUM features requiring paid subscription.

Sensor entities for premium features (analytics, compliance, research).
These sensors are gated behind feature flags and show "locked" state for free tier.

Premium Tiers:
- Basic Plan: Analytics sensors (yield prediction, anomaly detection, performance)
- Professional Plan: Compliance sensors + Research sensors
- Enterprise Plan: All features + priority support

Free Tier Behavior:
- Sensors show "locked" state
- Icon changes to "mdi:lock"
- Attributes include upgrade_url and required_tier
- Entity disabled by default (can be manually enabled to see upgrade prompts)
"""

import logging
from typing import Any, Dict, Optional

from homeassistant.helpers.entity import Entity

from .const import DOMAIN
from .premium_entities import PremiumEntityMixin
from .sensor import CustomSensor

_LOGGER = logging.getLogger(__name__)


# ============================================================================
# ANALYTICS SENSORS
# ============================================================================


class YieldPredictionSensor(PremiumEntityMixin, CustomSensor):
    """
    Sensor showing AI-powered yield prediction.

    Requires: Basic plan (advanced_analytics feature)
    State: Expected yield in grams
    Attributes: min, max, confidence, model_version
    """

    def __init__(self, room_name: str, coordinator):
        """Initialize yield prediction sensor."""
        super().__init__(
            name=f"OGB_Yield_Prediction_{room_name}",
            room_name=room_name,
            coordinator=coordinator,
            initial_value="locked",
            device_class=None,
        )

        # Premium feature configuration
        self.required_feature = "advanced_analytics"
        self.required_tier = "basic"

        # Sensor-specific attributes
        self._prediction_min = None
        self._prediction_max = None
        self._confidence = None
        self._model_version = None
        self._last_update = None

        _LOGGER.info(f"✅ YieldPredictionSensor initialized for {room_name}")

    @property
    def unit_of_measurement(self):
        """Return grams as unit."""
        return "g"

    @property
    def icon(self):
        """Return appropriate icon."""
        if not self._has_feature_access():
            return "mdi:lock"
        return "mdi:chart-line"

    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        """Return prediction details."""
        attrs = super().extra_state_attributes

        if self._has_feature_access():
            attrs.update(
                {
                    "prediction_min": self._prediction_min,
                    "prediction_max": self._prediction_max,
                    "confidence": self._confidence,
                    "model_version": self._model_version,
                    "last_update": self._last_update,
                }
            )

        return attrs

    def update_prediction(self, data: Dict[str, Any]):
        """
        Update prediction from analytics module.

        Args:
            data: Dict with keys: expected, min, max, confidence, model_version
        """
        if not self._has_feature_access():
            _LOGGER.debug(
                f"{self.room_name}: Cannot update yield prediction (feature locked)"
            )
            return

        try:
            self._state = data.get("expected")
            self._prediction_min = data.get("min")
            self._prediction_max = data.get("max")
            self._confidence = data.get("confidence")
            self._model_version = data.get("model_version")
            self._last_update = data.get("timestamp")

            self.async_write_ha_state()
            _LOGGER.debug(f"{self.room_name}: Yield prediction updated: {self._state}g")

        except Exception as e:
            _LOGGER.error(f"{self.room_name}: Error updating yield prediction: {e}")


class AnomalyScoreSensor(PremiumEntityMixin, CustomSensor):
    """
    Sensor showing environmental anomaly detection score.

    Requires: Basic plan (advanced_analytics feature)
    State: Anomaly score 0-100 (higher = more anomalous)
    Attributes: detected_anomalies, severity, affected_metrics
    """

    def __init__(self, room_name: str, coordinator):
        """Initialize anomaly score sensor."""
        super().__init__(
            name=f"OGB_Anomaly_Score_{room_name}",
            room_name=room_name,
            coordinator=coordinator,
            initial_value="locked",
            device_class=None,
        )

        self.required_feature = "advanced_analytics"
        self.required_tier = "basic"

        self._detected_anomalies = []
        self._severity = None
        self._affected_metrics = []
        self._last_check = None

        _LOGGER.info(f"✅ AnomalyScoreSensor initialized for {room_name}")

    @property
    def unit_of_measurement(self):
        """Return percentage as unit."""
        return "%"

    @property
    def icon(self):
        """Return icon based on anomaly level."""
        if not self._has_feature_access():
            return "mdi:lock"

        # Dynamic icon based on score
        if isinstance(self._state, (int, float)):
            if self._state > 75:
                return "mdi:alert-circle"
            elif self._state > 50:
                return "mdi:alert"
            elif self._state > 25:
                return "mdi:information"

        return "mdi:check-circle"

    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        """Return anomaly details."""
        attrs = super().extra_state_attributes

        if self._has_feature_access():
            attrs.update(
                {
                    "detected_anomalies": self._detected_anomalies,
                    "severity": self._severity,
                    "affected_metrics": self._affected_metrics,
                    "last_check": self._last_check,
                }
            )

        return attrs

    def update_anomalies(self, data: Dict[str, Any]):
        """
        Update anomaly data from analytics module.

        Args:
            data: Dict with keys: score, anomalies, severity, affected_metrics
        """
        if not self._has_feature_access():
            return

        try:
            self._state = data.get("score", 0)
            self._detected_anomalies = data.get("anomalies", [])
            self._severity = data.get("severity", "none")
            self._affected_metrics = data.get("affected_metrics", [])
            self._last_check = data.get("timestamp")

            self.async_write_ha_state()
            _LOGGER.debug(f"{self.room_name}: Anomaly score updated: {self._state}%")

        except Exception as e:
            _LOGGER.error(f"{self.room_name}: Error updating anomaly score: {e}")


class PerformanceScoreSensor(PremiumEntityMixin, CustomSensor):
    """
    Sensor showing overall grow performance score.

    Requires: Basic plan (advanced_analytics feature)
    State: Performance score 0-100
    Attributes: accuracy, precision, recall, model_metrics
    """

    def __init__(self, room_name: str, coordinator):
        """Initialize performance score sensor."""
        super().__init__(
            name=f"OGB_Performance_Score_{room_name}",
            room_name=room_name,
            coordinator=coordinator,
            initial_value="locked",
            device_class=None,
        )

        self.required_feature = "advanced_analytics"
        self.required_tier = "basic"

        self._accuracy = None
        self._precision = None
        self._recall = None
        self._model_metrics = {}

        _LOGGER.info(f"✅ PerformanceScoreSensor initialized for {room_name}")

    @property
    def unit_of_measurement(self):
        """Return percentage as unit."""
        return "%"

    @property
    def icon(self):
        """Return icon based on performance."""
        if not self._has_feature_access():
            return "mdi:lock"

        if isinstance(self._state, (int, float)):
            if self._state >= 90:
                return "mdi:chart-box"
            elif self._state >= 70:
                return "mdi:chart-line"
            else:
                return "mdi:chart-line-variant"

        return "mdi:chart-box-outline"

    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        """Return performance metrics."""
        attrs = super().extra_state_attributes

        if self._has_feature_access():
            attrs.update(
                {
                    "accuracy": self._accuracy,
                    "precision": self._precision,
                    "recall": self._recall,
                    "model_metrics": self._model_metrics,
                }
            )

        return attrs

    def update_performance(self, data: Dict[str, Any]):
        """
        Update performance data from analytics module.

        Args:
            data: Dict with keys: score, accuracy, precision, recall, metrics
        """
        if not self._has_feature_access():
            return

        try:
            self._state = data.get("score", 0)
            self._accuracy = data.get("accuracy")
            self._precision = data.get("precision")
            self._recall = data.get("recall")
            self._model_metrics = data.get("metrics", {})

            self.async_write_ha_state()
            _LOGGER.debug(
                f"{self.room_name}: Performance score updated: {self._state}%"
            )

        except Exception as e:
            _LOGGER.error(f"{self.room_name}: Error updating performance score: {e}")


# ============================================================================
# COMPLIANCE SENSORS
# ============================================================================


class ComplianceStatusSensor(PremiumEntityMixin, CustomSensor):
    """
    Sensor showing regulatory compliance status.

    Requires: Professional plan (compliance feature)
    State: "compliant" | "non_compliant" | "needs_review" | "locked"
    Attributes: violations_count, industry, last_audit, rules_checked
    """

    def __init__(self, room_name: str, coordinator):
        """Initialize compliance status sensor."""
        super().__init__(
            name=f"OGB_Compliance_Status_{room_name}",
            room_name=room_name,
            coordinator=coordinator,
            initial_value="locked",
            device_class=None,
        )

        self.required_feature = "compliance"
        self.required_tier = "professional"

        self._violations_count = 0
        self._industry = None
        self._last_audit = None
        self._rules_checked = 0
        self._violations = []

        _LOGGER.info(f"✅ ComplianceStatusSensor initialized for {room_name}")

    @property
    def icon(self):
        """Return icon based on compliance status."""
        if not self._has_feature_access():
            return "mdi:lock"

        if self._state == "compliant":
            return "mdi:shield-check"
        elif self._state == "non_compliant":
            return "mdi:shield-alert"
        elif self._state == "needs_review":
            return "mdi:shield-half-full"

        return "mdi:shield"

    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        """Return compliance details."""
        attrs = super().extra_state_attributes

        if self._has_feature_access():
            attrs.update(
                {
                    "violations_count": self._violations_count,
                    "industry": self._industry,
                    "last_audit": self._last_audit,
                    "rules_checked": self._rules_checked,
                    "violations": self._violations,
                }
            )

        return attrs

    def update_compliance(self, data: Dict[str, Any]):
        """
        Update compliance data from compliance module.

        Args:
            data: Dict with keys: is_compliant, violations, industry, timestamp
        """
        if not self._has_feature_access():
            return

        try:
            is_compliant = data.get("is_compliant", False)
            violations = data.get("violations", [])

            # Determine state
            if is_compliant:
                self._state = "compliant"
            elif len(violations) > 0:
                self._state = "non_compliant"
            else:
                self._state = "needs_review"

            self._violations_count = len(violations)
            self._violations = violations
            self._industry = data.get("industry")
            self._last_audit = data.get("timestamp")
            self._rules_checked = data.get("rules_checked", 0)

            self.async_write_ha_state()
            _LOGGER.debug(f"{self.room_name}: Compliance status updated: {self._state}")

        except Exception as e:
            _LOGGER.error(f"{self.room_name}: Error updating compliance status: {e}")


class ViolationsCountSensor(PremiumEntityMixin, CustomSensor):
    """
    Sensor showing count of compliance violations.

    Requires: Professional plan (compliance feature)
    State: Number of active violations
    Attributes: violation_details, severity_breakdown
    """

    def __init__(self, room_name: str, coordinator):
        """Initialize violations count sensor."""
        super().__init__(
            name=f"OGB_Compliance_Violations_{room_name}",
            room_name=room_name,
            coordinator=coordinator,
            initial_value="locked",
            device_class=None,
        )

        self.required_feature = "compliance"
        self.required_tier = "professional"

        self._violation_details = []
        self._severity_breakdown = {}

        _LOGGER.info(f"✅ ViolationsCountSensor initialized for {room_name}")

    @property
    def unit_of_measurement(self):
        """Return violations as unit."""
        return "violations"

    @property
    def icon(self):
        """Return icon based on violation count."""
        if not self._has_feature_access():
            return "mdi:lock"

        if isinstance(self._state, int):
            if self._state == 0:
                return "mdi:check-circle"
            elif self._state < 3:
                return "mdi:alert"
            else:
                return "mdi:alert-circle"

        return "mdi:information"

    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        """Return violation details."""
        attrs = super().extra_state_attributes

        if self._has_feature_access():
            attrs.update(
                {
                    "violation_details": self._violation_details,
                    "severity_breakdown": self._severity_breakdown,
                }
            )

        return attrs

    def update_violations(self, data: Dict[str, Any]):
        """
        Update violations data from compliance module.

        Args:
            data: Dict with keys: violations (list of violation objects)
        """
        if not self._has_feature_access():
            return

        try:
            violations = data.get("violations", [])
            self._state = len(violations)
            self._violation_details = violations

            # Calculate severity breakdown
            severity_breakdown = {"low": 0, "medium": 0, "high": 0, "critical": 0}
            for violation in violations:
                severity = violation.get("severity", "low")
                severity_breakdown[severity] = severity_breakdown.get(severity, 0) + 1

            self._severity_breakdown = severity_breakdown

            self.async_write_ha_state()
            _LOGGER.debug(f"{self.room_name}: Violations count updated: {self._state}")

        except Exception as e:
            _LOGGER.error(f"{self.room_name}: Error updating violations count: {e}")


# ============================================================================
# RESEARCH SENSORS
# ============================================================================


class DatasetCountSensor(PremiumEntityMixin, CustomSensor):
    """
    Sensor showing count of research datasets.

    Requires: Professional plan (research_data feature)
    State: Number of active datasets
    Attributes: datasets, status_breakdown
    """

    def __init__(self, room_name: str, coordinator):
        """Initialize dataset count sensor."""
        super().__init__(
            name=f"OGB_Research_Datasets_{room_name}",
            room_name=room_name,
            coordinator=coordinator,
            initial_value="locked",
            device_class=None,
        )

        self.required_feature = "research_data"
        self.required_tier = "professional"

        self._datasets = []
        self._status_breakdown = {}

        _LOGGER.info(f"✅ DatasetCountSensor initialized for {room_name}")

    @property
    def unit_of_measurement(self):
        """Return datasets as unit."""
        return "datasets"

    @property
    def icon(self):
        """Return database icon."""
        if not self._has_feature_access():
            return "mdi:lock"
        return "mdi:database"

    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        """Return dataset details."""
        attrs = super().extra_state_attributes

        if self._has_feature_access():
            attrs.update(
                {
                    "datasets": self._datasets,
                    "status_breakdown": self._status_breakdown,
                }
            )

        return attrs

    def update_datasets(self, data: Dict[str, Any]):
        """
        Update datasets from research module.

        Args:
            data: Dict with keys: datasets (list of dataset objects)
        """
        if not self._has_feature_access():
            return

        try:
            datasets = data.get("datasets", [])
            self._state = len(datasets)
            self._datasets = datasets

            # Calculate status breakdown
            status_breakdown = {"active": 0, "completed": 0, "archived": 0}
            for dataset in datasets:
                status = dataset.get("status", "active")
                status_breakdown[status] = status_breakdown.get(status, 0) + 1

            self._status_breakdown = status_breakdown

            self.async_write_ha_state()
            _LOGGER.debug(f"{self.room_name}: Dataset count updated: {self._state}")

        except Exception as e:
            _LOGGER.error(f"{self.room_name}: Error updating dataset count: {e}")


class DataQualitySensor(PremiumEntityMixin, CustomSensor):
    """
    Sensor showing research data quality score.

    Requires: Professional plan (research_data feature)
    State: Quality score 0-100
    Attributes: completeness, accuracy, consistency
    """

    def __init__(self, room_name: str, coordinator):
        """Initialize data quality sensor."""
        super().__init__(
            name=f"OGB_Data_Quality_Score_{room_name}",
            room_name=room_name,
            coordinator=coordinator,
            initial_value="locked",
            device_class=None,
        )

        self.required_feature = "research_data"
        self.required_tier = "professional"

        self._completeness = None
        self._accuracy = None
        self._consistency = None

        _LOGGER.info(f"✅ DataQualitySensor initialized for {room_name}")

    @property
    def unit_of_measurement(self):
        """Return percentage as unit."""
        return "%"

    @property
    def icon(self):
        """Return icon based on quality."""
        if not self._has_feature_access():
            return "mdi:lock"

        if isinstance(self._state, (int, float)):
            if self._state >= 90:
                return "mdi:quality-high"
            elif self._state >= 70:
                return "mdi:quality-medium"
            else:
                return "mdi:quality-low"

        return "mdi:information"

    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        """Return quality metrics."""
        attrs = super().extra_state_attributes

        if self._has_feature_access():
            attrs.update(
                {
                    "completeness": self._completeness,
                    "accuracy": self._accuracy,
                    "consistency": self._consistency,
                }
            )

        return attrs

    def update_quality(self, data: Dict[str, Any]):
        """
        Update quality data from research module.

        Args:
            data: Dict with keys: quality_score, completeness, accuracy, consistency
        """
        if not self._has_feature_access():
            return

        try:
            self._state = data.get("quality_score", 0)
            self._completeness = data.get("completeness")
            self._accuracy = data.get("accuracy")
            self._consistency = data.get("consistency")

            self.async_write_ha_state()
            _LOGGER.debug(f"{self.room_name}: Data quality updated: {self._state}%")

        except Exception as e:
            _LOGGER.error(f"{self.room_name}: Error updating data quality: {e}")
