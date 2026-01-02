"""
OpenGrowBox Premium Research Module

Handles research data collection, dataset management, and quality metrics.
Provides methods for dataset tracking, research data submission,
and quality assessment.
"""

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

_LOGGER = logging.getLogger(__name__)


class OGBPremResearch:
    """
    Research module for OpenGrowBox premium features.

    Provides research capabilities including:
    - Dataset management and tracking
    - Research data collection
    - Quality metrics calculation
    - Dataset listing and metadata
    """

    def __init__(self, api_proxy=None, cache=None):
        """
        Initialize research module.

        Args:
            api_proxy: API proxy for backend communication
            cache: Cache instance for data storage
        """
        self.api_proxy = api_proxy
        self.cache = cache

        # Cache keys
        self._datasets_key = "research_datasets"
        self._quality_key = "research_quality"

        # Cache TTL (6 hours for research data)
        self._cache_ttl = 21600

        # Research data storage
        self._datasets = []
        self._quality_reports = {}

    async def list_datasets(self) -> List[Dict[str, Any]]:
        """
        List available research datasets.

        Returns:
            List of dataset metadata
        """
        try:
            # Check cache first
            if self.cache:
                cached = await self.cache.get(self._datasets_key)
                if cached:
                    return cached

            # Fetch from API
            if self.api_proxy:
                result = await self.api_proxy.get_research_data("datasets")

                if result.get("success"):
                    datasets = result.get("data", [])

                    # Cache the result
                    if self.cache:
                        await self.cache.set(
                            self._datasets_key, datasets, ttl=self._cache_ttl
                        )

                    self._datasets = datasets
                    return datasets

            return []

        except Exception as e:
            _LOGGER.error(f"Error listing datasets: {e}")
            return []

    async def get_dataset_count(self) -> int:
        """
        Get the total number of available datasets.

        Returns:
            Number of datasets
        """
        try:
            datasets = await self.list_datasets()
            return len(datasets)

        except Exception as e:
            _LOGGER.error(f"Error getting dataset count: {e}")
            return 0

    async def get_quality_report(self, dataset_id: str) -> Dict[str, Any]:
        """
        Get quality report for a specific dataset.

        Args:
            dataset_id: ID of the dataset

        Returns:
            Dictionary with quality metrics
        """
        try:
            cache_key = f"{self._quality_key}_{dataset_id}"

            # Check cache first
            if self.cache:
                cached = await self.cache.get(cache_key)
                if cached:
                    return cached

            # Fetch from API
            if self.api_proxy:
                result = await self.api_proxy.get_research_data(
                    "quality", {"dataset_id": dataset_id}
                )

                if result.get("success"):
                    quality_data = result.get("data", {})

                    # Cache the result
                    if self.cache:
                        await self.cache.set(
                            cache_key, quality_data, ttl=self._cache_ttl
                        )

                    self._quality_reports[dataset_id] = quality_data
                    return quality_data

            # Generate basic quality metrics if no API data
            return await self._generate_basic_quality_report(dataset_id)

        except Exception as e:
            _LOGGER.error(f"Error getting quality report for dataset {dataset_id}: {e}")
            return await self._generate_basic_quality_report(dataset_id)

    async def _generate_basic_quality_report(self, dataset_id: str) -> Dict[str, Any]:
        """
        Generate basic quality report when API is unavailable.

        Args:
            dataset_id: ID of the dataset

        Returns:
            Basic quality report
        """
        # Find dataset metadata
        dataset_info = None
        for dataset in self._datasets:
            if dataset.get("id") == dataset_id:
                dataset_info = dataset
                break

        if not dataset_info:
            return {
                "dataset_id": dataset_id,
                "quality_score": 50,
                "completeness": 0.5,
                "accuracy": 0.5,
                "timeliness": 0.5,
                "error": "Dataset not found",
            }

        # Calculate basic quality metrics
        completeness = self._calculate_completeness(dataset_info)
        accuracy = self._calculate_accuracy(dataset_info)
        timeliness = self._calculate_timeliness(dataset_info)

        # Overall quality score (weighted average)
        quality_score = (completeness * 0.4 + accuracy * 0.4 + timeliness * 0.2) * 100

        return {
            "dataset_id": dataset_id,
            "dataset_name": dataset_info.get("name", "Unknown"),
            "quality_score": round(quality_score, 1),
            "completeness": round(completeness, 2),
            "accuracy": round(accuracy, 2),
            "timeliness": round(timeliness, 2),
            "total_records": dataset_info.get("record_count", 0),
            "last_updated": dataset_info.get("last_updated"),
            "generated_at": datetime.now().isoformat(),
        }

    async def handle_dataset_update(self, update_data: Dict[str, Any]):
        """
        Handle dataset update from WebSocket or API.

        Args:
            update_data: Update data from the system
        """
        try:
            dataset_id = update_data.get("dataset_id")
            update_type = update_data.get("update_type", "unknown")
            dataset_name = update_data.get("dataset_name", "Unknown")

            _LOGGER.info(
                f"📊 Dataset Update [{update_type}]: {dataset_name} ({dataset_id})"
            )

            # Invalidate relevant caches
            if self.cache:
                await self.cache.delete(self._datasets_key)
                if dataset_id:
                    await self.cache.delete(f"{self._quality_key}_{dataset_id}")

            # Update local dataset list
            await self.list_datasets()

        except Exception as e:
            _LOGGER.error(f"Error handling dataset update: {e}")

    async def submit_research_data(self, data: Dict[str, Any], feature_manager=None) -> bool:
        """
        Submit research data to the backend.

        Args:
            data: Research data to submit
            feature_manager: Optional feature manager for access control

        Returns:
            True if submission was successful
        """
        # Check feature access
        if feature_manager and not feature_manager.has_feature("research_data"):
            raise Exception("Research data feature not available")

        try:
            if not self.api_proxy:
                _LOGGER.warning("No API proxy available for research data submission")
                return False

            result = await self.api_proxy.submit_research_data(data)

            if result.get("success"):
                _LOGGER.info("Research data submitted successfully")
                return True
            else:
                _LOGGER.error(
                    f"Research data submission failed: {result.get('message')}"
                )
                return False

        except Exception as e:
            _LOGGER.error(f"Error submitting research data: {e}")
            return False

    async def get_data_quality_score(self) -> float:
        """
        Get overall data quality score across all datasets.

        Returns:
            Quality score (0-100)
        """
        try:
            datasets = await self.list_datasets()

            if not datasets:
                return 0.0

            total_score = 0.0
            count = 0

            for dataset in datasets:
                dataset_id = dataset.get("id")
                if dataset_id:
                    quality_report = await self.get_quality_report(dataset_id)
                    total_score += quality_report.get("quality_score", 50)
                    count += 1

            return round(total_score / max(count, 1), 1)

        except Exception as e:
            _LOGGER.error(f"Error calculating data quality score: {e}")
            return 50.0

    def _calculate_completeness(self, dataset_info: Dict[str, Any]) -> float:
        """
        Calculate completeness score for a dataset.

        Args:
            dataset_info: Dataset metadata

        Returns:
            Completeness score (0-1)
        """
        # Placeholder calculation based on available metadata
        record_count = dataset_info.get("record_count", 0)
        expected_fields = dataset_info.get("expected_fields", 1)
        actual_fields = dataset_info.get("actual_fields", expected_fields)

        if expected_fields == 0:
            return 1.0

        # Basic completeness based on field coverage
        field_completeness = min(actual_fields / expected_fields, 1.0)

        # Basic record completeness (placeholder)
        record_completeness = min(record_count / 1000, 1.0) if record_count > 0 else 0.0

        return field_completeness * 0.7 + record_completeness * 0.3

    def _calculate_accuracy(self, dataset_info: Dict[str, Any]) -> float:
        """
        Calculate accuracy score for a dataset.

        Args:
            dataset_info: Dataset metadata

        Returns:
            Accuracy score (0-1)
        """
        # Placeholder calculation
        # In a real system, this would analyze data validation results

        # Use dataset age as proxy (newer datasets assumed more accurate)
        last_updated = dataset_info.get("last_updated")
        if last_updated:
            try:
                update_time = datetime.fromisoformat(last_updated)
                days_old = (datetime.now() - update_time).days

                # Datasets less than 30 days old get higher accuracy score
                if days_old < 30:
                    return 0.9
                elif days_old < 90:
                    return 0.7
                else:
                    return 0.5
            except (ValueError, TypeError):
                pass

        return 0.6  # Default accuracy score

    def _calculate_timeliness(self, dataset_info: Dict[str, Any]) -> float:
        """
        Calculate timeliness score for a dataset.

        Args:
            dataset_info: Dataset metadata

        Returns:
            Timeliness score (0-1)
        """
        # Placeholder calculation based on update frequency

        last_updated = dataset_info.get("last_updated")
        if last_updated:
            try:
                update_time = datetime.fromisoformat(last_updated)
                days_since_update = (datetime.now() - update_time).days

                # Datasets updated within last 7 days get perfect score
                if days_since_update <= 7:
                    return 1.0
                elif days_since_update <= 30:
                    return 0.8
                elif days_since_update <= 90:
                    return 0.6
                else:
                    return 0.3
            except (ValueError, TypeError):
                pass

        return 0.5  # Default timeliness score
