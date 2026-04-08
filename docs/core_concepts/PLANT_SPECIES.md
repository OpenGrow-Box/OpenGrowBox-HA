# Plant Species and Growth Stages

## Overview

OpenGrowBox supports **20 different plant species** with species-specific VPD (Vapor Pressure Deficit) maps and growth stages. Each species has optimized environmental parameters for temperature, humidity, VPD, EC, pH, light, and CO2.

## Supported Plant Species

### Fruiting Plants (with Flowering Stages)
These plants have 8 growth stages including flowering phases:

1. **Cannabis** - Medical/recreational cannabis cultivation
2. **Tomato** - Tomatoes and cherry tomatoes
3. **Cucumber** - Cucumbers and gherkins
4. **Pepper** - Bell peppers and chili peppers
5. **Strawberry** - Strawberries and wild strawberries
6. **Broccoli** - Broccoli and cauliflower
7. **Zucchini** - Zucchini and summer squash

### Leafy Greens (Vegetative Only)
These plants have 5 growth stages (no flowering):

8. **Arugula** - Rocket salad, fast-growing
9. **Basil** - Sweet basil and Genovese basil
10. **Chard** - Swiss chard and rainbow chard
11. **Cilantro** - Coriander leaves
12. **Kale** - Curly and lacinato kale
13. **Lettuce** - Leaf lettuce, romaine, butterhead
14. **Microgreens** - Mixed microgreens (short cycle)
15. **Mint** - Peppermint and spearmint
16. **Oregano** - Mediterranean oregano
17. **Parsley** - Flat-leaf and curly parsley
18. **Spinach** - Baby spinach and mature spinach
19. **Thyme** - Common thyme and lemon thyme

## Growth Stages

### Full Cycle (8 Stages) - Fruiting Plants

| Stage | Duration | Key Characteristics |
|-------|----------|---------------------|
| **Germination** | 3-7 days | Seed sprouting, high humidity (70-85%), minimal light |
| **Clones** | 7-14 days | Root development, high humidity (70-80%), low light |
| **EarlyVeg** | 1-2 weeks | Initial vegetative growth, establishing roots |
| **MidVeg** | 2-4 weeks | Rapid growth, developing canopy |
| **LateVeg** | 1-2 weeks | Pre-flowering, final vegetative growth |
| **EarlyFlower** | 2-3 weeks | Transition to flowering, bud formation |
| **MidFlower** | 3-5 weeks | Peak flowering, fruit development |
| **LateFlower** | 2-3 weeks | Ripening, harvest preparation |

### Vegetative Only (5 Stages) - Leafy Greens & Herbs

| Stage | Duration | Key Characteristics |
|-------|----------|---------------------|
| **Germination** | 3-7 days | Seed sprouting, very high humidity |
| **Clones** | 7-14 days | Rooting cuttings, high humidity |
| **EarlyVeg** | 1-2 weeks | Young plant establishment |
| **MidVeg** | 2-4 weeks | Active growth phase |
| **LateVeg** | 1-3 weeks | Mature growth, harvest ready |

## Species-Specific VPD Parameters

### Example: Cannabis VPD Ranges

| Stage | VPD Range | Temperature | Humidity | EC Range |
|-------|-----------|-------------|----------|----------|
| Germination | 0.35 - 0.70 | 20-24°C | 78-85% | 0.6 - 0.9 |
| Clones | 0.40 - 0.85 | 20-24°C | 72-80% | 0.8 - 1.2 |
| EarlyVeg | 0.60 - 1.20 | 22-26°C | 65-75% | 1.0 - 1.6 |
| MidVeg | 0.75 - 1.45 | 23-27°C | 60-72% | 1.2 - 1.8 |
| LateVeg | 0.90 - 1.65 | 24-27°C | 55-68% | 1.4 - 2.0 |
| EarlyFlower | 0.80 - 1.55 | 22-26°C | 55-68% | 1.6 - 2.2 |
| MidFlower | 0.90 - 1.70 | 21-25°C | 38-52% | 1.8 - 2.4 |
| LateFlower | 0.90 - 1.85 | 20-26°C | 40-55% | 1.4 - 2.0 |

### Example: Lettuce VPD Ranges (Vegetative Only)

| Stage | VPD Range | Temperature | Humidity | EC Range |
|-------|-----------|-------------|----------|----------|
| Germination | 0.40 - 0.80 | 15-20°C | 70-80% | 0.6 - 1.0 |
| Clones | 0.50 - 0.90 | 15-20°C | 65-75% | 0.8 - 1.2 |
| EarlyVeg | 0.60 - 1.00 | 16-20°C | 60-70% | 1.0 - 1.4 |
| MidVeg | 0.70 - 1.10 | 16-20°C | 55-65% | 1.2 - 1.6 |
| LateVeg | 0.80 - 1.20 | 16-20°C | 50-60% | 1.0 - 1.4 |

## Using Plant Species

### Selecting a Species

1. Navigate to your room's control panel
2. Find the **OGB_PlantSpecies** select entity
3. Choose your plant species from the dropdown

### What Happens When You Change Species

When you select a different plant species:

1. **PlantStage options update** - Only relevant stages for that species are shown
2. **VPD targets change** - Species-specific VPD ranges are loaded
3. **Environmental parameters update** - Temperature, humidity, EC, pH targets adjust
4. **Current stage may change** - If current stage doesn't exist in new species, it resets to Germination

### Example: Switching from Cannabis to Lettuce

**Before (Cannabis - LateFlower):**
- Available stages: Germination, Clones, EarlyVeg, MidVeg, LateVeg, EarlyFlower, MidFlower, LateFlower
- Current stage: LateFlower
- VPD target: 0.90-1.85 kPa

**After switching to Lettuce:**
- Available stages: Germination, Clones, EarlyVeg, MidVeg, LateVeg
- Current stage: Germination (LateFlower doesn't exist for Lettuce)
- VPD target: 0.40-0.80 kPa

## Configuration

### Default Species

The default plant species is **Cannabis** if no species is selected.

### Per-Room Configuration

Each room can have its own plant species:
- Room 1: Cannabis
- Room 2: Lettuce
- Room 3: Tomatoes

### Data Storage

Species configuration is stored in the datastore:

```json
{
  "plantSpecies": "Cannabis",
  "plantStage": "MidVeg",
  "plantStages": {
    "MidVeg": {
      "vpdRange": [0.75, 1.45],
      "minTemp": 23,
      "maxTemp": 27,
      "minHumidity": 60,
      "maxHumidity": 72,
      "minEC": 1.2,
      "maxEc": 1.8,
      "minPh": 5.8,
      "maxPh": 6.2,
      "minLight": 40,
      "maxLight": 60,
      "minCo2": 600,
      "maxCo2": 1000
    }
  }
}
```

## Adding Custom Species

To add a new plant species:

1. Edit `custom_components/opengrowbox/OGBController/data/OGBParams/OGBPlants.py`
2. Add species name to `PLANT_SPECIES_OPTIONS`
3. Add VPD map to `PLANT_SPECIES_VPD_MAPS`
4. Restart Home Assistant

### Example: Adding a New Species

```python
PLANT_SPECIES_OPTIONS = [
    # ... existing species ...
    "Eggplant",  # Add new species
]

PLANT_SPECIES_VPD_MAPS = {
    # ... existing species ...
    "Eggplant": {
        "Germination": {
            "vpdRange": [0.40, 0.80],
            "minTemp": 20,
            "maxTemp": 25,
            "minHumidity": 70,
            "maxHumidity": 80,
            "minEC": 0.8,
            "maxEc": 1.2,
            "minPh": 5.5,
            "maxPh": 6.5,
            "minLight": 20,
            "maxLight": 30,
            "minCo2": 400,
            "maxCo2": 800,
        },
        # ... add other stages ...
    }
}
```

## Troubleshooting

### PlantStage Select Not Updating

**Problem**: When changing species, PlantStage options don't update

**Solution**: 
- Check that `set_select_options` service is registered
- Verify the PlantStage select entity exists for the room
- Check logs for errors in `_update_plant_stage_select_options`

### VPD Targets Not Changing

**Problem**: VPD targets remain the same after species change

**Solution**:
- Verify species is valid in `PLANT_SPECIES_VPD_MAPS`
- Check that `PlantSpeciesChange` event is emitted
- Confirm VPD manager is processing the event

### Missing Stages

**Problem**: Some stages are missing for a species

**Solution**:
- Leafy greens and herbs intentionally have fewer stages (no flowering)
- Check `get_plant_species_stages()` returns correct stages
- Verify species configuration in `OGBPlants.py`

## References

- **VPD Theory**: See [VPD Control Modes](ACTION_CYCLE_VPD_MODES.md)
- **Plant Stage Details**: See [Control Modes](CONTROL_MODES.md)
- **Configuration**: See [Configuration Guide](../configuration/CONFIGURATION.md)

---

**Last Updated**: January 2025
**Version**: 1.0
**Status**: Production Ready
