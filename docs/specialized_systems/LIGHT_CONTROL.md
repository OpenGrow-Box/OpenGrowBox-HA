# Advanced Lighting Control System - DLI & Spectrum Management

## Overview

The Advanced Lighting Control System provides comprehensive lighting management for optimal plant growth. It includes Daily Light Integral (DLI) calculation, spectrum control, sunrise/sunset transitions, and plant stage adaptation for maximum photosynthesis efficiency.

## System Architecture

### Core Components

#### 1. Light Controller (`Light.py`)
```python
class Light(Device):
    """Advanced light controller with DLI and spectrum management."""
```

#### 2. Light Spectrum Controller (`LightSpectrum.py`)
```python
class LightSpectrum:
    """Spectrum-specific lighting control."""
```

#### 3. Specialized Light Controllers
- **LightUV**: UV spectrum control
- **LightFarRed**: Far-red spectrum enhancement
- **LightBlue/LightRed**: Primary spectrum control

## Lighting Fundamentals

### Photosynthetically Active Radiation (PAR)

PAR represents the portion of light (400-700nm) used for photosynthesis:

- **PPFD**: Photosynthetic Photon Flux Density (μmol/m²/s)
- **DLI**: Daily Light Integral (mol/m²/day)
- **Spectrum**: Light wavelength composition

### Plant Lighting Requirements

#### By Growth Stage

```python
PLANT_LIGHT_REQUIREMENTS = {
    "Germination": {
        "ppfd_range": [50, 150],      # Low light for seedling development
        "dli_target": [8, 12],        # mol/m²/day
        "spectrum": "balanced",       # Full spectrum
        "photoperiod": "18/6"         # 18 hours light, 6 hours dark
    },
    "EarlyVeg": {</content>
<parameter name="filePath">docs/specialized_systems/LIGHT_CONTROL.md