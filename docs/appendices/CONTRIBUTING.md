# Contributing to OpenGrowBox

Thank you for your interest in contributing to OpenGrowBox! This document provides guidelines and information for contributors.

## Table of Contents

- [Code of Conduct](#code-of-conduct)
- [Getting Started](#getting-started)
- [Development Environment](#development-environment)
- [Development Workflow](#development-workflow)
- [Coding Standards](#coding-standards)
- [Testing](#testing)
- [Documentation](#documentation)
- [Submitting Changes](#submitting-changes)
- [Reporting Issues](#reporting-issues)

## Code of Conduct

This project follows a code of conduct to ensure a welcoming environment for all contributors. By participating, you agree to:

- Be respectful and inclusive
- Focus on constructive feedback
- Accept responsibility for mistakes
- Show empathy towards other contributors
- Help create a positive community

## Getting Started

### Prerequisites

- Python 3.9 or higher
- Home Assistant development environment
- Git
- ESPHome (for device development)
- Docker (recommended for testing)

### Fork and Clone

1. Fork the repository on GitHub
2. Clone your fork locally:
   ```bash
   git clone https://github.com/your-username/opengrowbox.git
   cd opengrowbox
   ```

3. Set up the upstream remote:
   ```bash
   git remote add upstream https://github.com/original-owner/opengrowbox.git
   ```

### Development Environment Setup

1. **Create a virtual environment:**
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```

2. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   pip install -r requirements-dev.txt
   ```

3. **Set up pre-commit hooks:**
   ```bash
   pip install pre-commit
   pre-commit install
   ```

4. **Home Assistant Development Setup:**
   ```bash
   # Use the HA development container
   docker run -d --name hass-dev \
     -p 8123:8123 \
     -v $(pwd)/custom_components:/config/custom_components \
     homeassistant/home-assistant:dev
   ```

## Development Workflow

### Branching Strategy

We use a simplified Git flow:

- `main`: Production-ready code
- `develop`: Integration branch for features
- `feature/*`: Feature branches
- `bugfix/*`: Bug fix branches
- `hotfix/*`: Critical fixes for production

### Creating a Feature Branch

```bash
# Start from develop branch
git checkout develop
git pull upstream develop

# Create feature branch
git checkout -b feature/your-feature-name

# Make your changes...

# Commit your work
git add .
git commit -m "feat: add your feature description"

# Push to your fork
git push origin feature/your-feature-name
```

### Pull Request Process

1. **Ensure your branch is up to date:**
   ```bash
   git fetch upstream
   git rebase upstream/develop
   ```

2. **Run tests and linting:**
   ```bash
   # Run all tests
   python -m pytest tests/ -v

   # Run linting
   flake8 custom_components/opengrowbox/
   black custom_components/opengrowbox/ --check
   mypy custom_components/opengrowbox/
   ```

3. **Update documentation if needed**

4. **Create a pull request:**
   - Use a clear, descriptive title
   - Provide detailed description of changes
   - Reference any related issues
   - Include screenshots for UI changes

## Coding Standards

### Python Style Guide

We follow PEP 8 with some modifications:

- **Line length:** 88 characters (Black default)
- **Imports:** Grouped and sorted
- **Docstrings:** Google-style docstrings
- **Type hints:** Required for new code

#### Import Organization

```python
# Standard library imports
import asyncio
import logging
from typing import Any, Dict, Optional

# Third-party imports
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

# Local imports
from .const import DOMAIN
from .coordinator import OGBDataCoordinator
```

#### Docstring Format

```python
def calculate_vpd(self, temperature: float, humidity: float) -> float:
    """Calculate vapor pressure deficit from temperature and humidity.

    Uses the Magnus-Tetens approximation for saturation vapor pressure.

    Args:
        temperature: Air temperature in Celsius
        humidity: Relative humidity as percentage (0-100)

    Returns:
        Vapor pressure deficit in kPa

    Raises:
        ValueError: If temperature or humidity values are invalid

    Example:
        >>> calc = VPDCalculator()
        >>> calc.calculate_vpd(25.0, 60.0)
        1.25
    """
```

### Async/Await Patterns

All I/O operations must be async:

```python
# ✅ Good: Async function with await
async def get_sensor_data(self) -> Dict[str, float]:
    """Get sensor data asynchronously."""
    async with self.session.get(self.api_url) as response:
        return await response.json()

# ❌ Bad: Blocking I/O in async function
def get_sensor_data_blocking(self) -> Dict[str, float]:
    """Don't do this - blocks the event loop."""
    response = requests.get(self.api_url)  # Blocking!
    return response.json()
```

### Error Handling

```python
# ✅ Good: Specific exception handling
try:
    result = await self.perform_operation()
except ConnectionError:
    _LOGGER.error("Network connection failed")
    await self.retry_operation()
except ValueError as e:
    _LOGGER.error(f"Invalid data received: {e}")
    raise
except Exception as e:
    _LOGGER.error(f"Unexpected error: {e}")
    await self.cleanup()
    raise
```

### Logging

Use appropriate log levels and include context:

```python
# Debug: Detailed diagnostic information
_LOGGER.debug(f"Processing sensor data: {sensor_data}")

# Info: General information about system operation
_LOGGER.info(f"Connected to {len(devices)} devices")

# Warning: Warning conditions
_LOGGER.warning(f"Sensor calibration is {days} days old")

# Error: Error conditions
_LOGGER.error(f"Failed to connect to device {device_id}: {e}")

# Critical: System-threatening errors
_LOGGER.critical("Database connection lost - system unstable")
```

## Testing

### Test Structure

Tests are organized by type and component:

```
tests/
├── unit/                 # Unit tests (fast, isolated)
├── integration/          # Integration tests (medium speed)
├── system/              # System tests (slow, end-to-end)
├── fixtures/            # Test data and fixtures
└── conftest.py          # Test configuration
```

### Writing Tests

```python
import pytest
from unittest.mock import AsyncMock, Mock
from custom_components.opengrowbox.OGBController.sensors import VPDCalculator

class TestVPDCalculator:
    """Test VPD calculation logic."""

    @pytest.fixture
    def calculator(self):
        """Create calculator instance for testing."""
        return VPDCalculator()

    @pytest.mark.asyncio
    async def test_calculate_vpd_basic(self, calculator):
        """Test basic VPD calculation."""
        temperature = 25.0
        humidity = 60.0

        result = await calculator.calculate_vpd(temperature, humidity)

        # Verify result is reasonable
        assert isinstance(result, float)
        assert result > 0
        assert result < 5  # Typical VPD range

        # Verify precision
        expected = 1.25  # Calculated expected value
        assert abs(result - expected) < 0.01

    @pytest.mark.parametrize("temp,humidity,expected", [
        (20, 50, 0.87),
        (25, 60, 1.25),
        (30, 70, 1.96),
    ])
    async def test_calculate_vpd_parametrized(self, calculator, temp, humidity, expected):
        """Test VPD calculation with multiple parameter sets."""
        result = await calculator.calculate_vpd(temp, humidity)
        assert abs(result - expected) < 0.01
```

### Running Tests

```bash
# Run all tests
python -m pytest tests/ -v

# Run specific test file
pytest tests/unit/test_vpd_calculator.py -v

# Run with coverage
pytest tests/ --cov=custom_components/opengrowbox --cov-report=html

# Run integration tests only
pytest tests/integration/ -v

# Run tests in parallel (if available)
pytest tests/ -n auto
```

## Documentation

### Documentation Standards

- Use Markdown for all documentation
- Include code examples where appropriate
- Keep screenshots up to date
- Document breaking changes clearly
- Use consistent terminology

### API Documentation

Document all public APIs:

```python
def get_sensor_data(self, sensor_id: str) -> Optional[Dict[str, Any]]:
    """Retrieve current sensor data for a specific sensor.

    Args:
        sensor_id: Unique identifier for the sensor

    Returns:
        Dictionary containing sensor data, or None if sensor not found

    Raises:
        ConnectionError: If unable to connect to sensor
        ValueError: If sensor_id is invalid

    Example:
        >>> data = await sensor.get_sensor_data("temp_1")
        >>> print(data)
        {'temperature': 25.5, 'humidity': 62.3, 'timestamp': 1640995200}
    """
```

## Submitting Changes

### Commit Message Format

We follow conventional commit format:

```
type(scope): description

[optional body]

[optional footer]
```

**Types:**
- `feat`: New feature
- `fix`: Bug fix
- `docs`: Documentation changes
- `style`: Code style changes (formatting, etc.)
- `refactor`: Code refactoring
- `test`: Adding or fixing tests
- `chore`: Maintenance tasks

**Examples:**
```
feat: add VPD perfection mode control algorithm

fix: resolve memory leak in sensor polling

docs: update API reference for new endpoints

test: add integration tests for device discovery
```

### Pull Request Guidelines

**Title:** Clear, descriptive summary of changes

**Description:**
- What problem does this solve?
- How was it implemented?
- What tests were added?
- Any breaking changes?
- Screenshots for UI changes

**Checklist:**
- [ ] Tests pass locally
- [ ] Code follows style guidelines
- [ ] Documentation updated
- [ ] Breaking changes documented
- [ ] Reviewed by at least one maintainer

## Reporting Issues

### Bug Reports

Please include:

1. **Clear title** describing the issue
2. **Steps to reproduce** the problem
3. **Expected behavior** vs actual behavior
4. **System information:**
   - OpenGrowBox version
   - Home Assistant version
   - Python version
   - Hardware configuration
5. **Logs** from the time of the issue
6. **Screenshots** if applicable

### Feature Requests

Please include:

1. **Clear description** of the proposed feature
2. **Use case** - why is this needed?
3. **Implementation ideas** if you have any
4. **Mockups** or examples if applicable

### Security Issues

For security-related issues, please email security@opengrowbox.com instead of creating a public issue.

## Recognition

Contributors will be recognized in:
- CHANGELOG.md for significant contributions
- GitHub repository contributors list
- Project documentation

Thank you for contributing to OpenGrowBox! 🎉</content>
<parameter name="filePath">docs/appendices/CONTRIBUTING.md