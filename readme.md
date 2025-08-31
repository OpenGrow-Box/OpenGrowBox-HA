# OpenGrowBox - Home Assistant Integration

⚙️ [Installation Guide](https://github.com/OpenGrow-Box/OpenGrowBox/wiki/Installation#-opengrowbox--installation-guide)
📖 [WIKI](https://github.com/OpenGrow-Box/OpenGrowBox/wiki/)

## Overview

Welcome to the **OpenGrowBox Home Assistant Integration** repository! This project is designed to seamlessly integrate the OpenGrowBox system with Home Assistant, allowing you to control and monitor your grow environment directly from your smart home setup.

---

## Features

- **VPD Control:** Automate Vapor Pressure Deficit (VPD) calculations and adjustments.
- **Device Management:** Manage humidifiers, dehumidifiers, heaters, coolers, and lights effortlessly.
- **Growth Stage Optimization:** Tailored settings for Germination, Vegetative, and Flowering stages.
- **Drying Modes:** Post-harvest drying options like `elClassico`, `sharkMouse`, and `dewBased`.
- **CO₂ Management:** Maintain CO₂ levels within an optimal range.
- **Custom Automation:** Fine-tune settings using Home Assistant automation scripts.

---

## Getting Started

### Prerequisites

- A working **Home Assistant** instance.
- OpenGrowBox hardware or simulated environment.
- Basic knowledge of Home Assistant configurations.

### Installation

1. Clone this repository:
   ```bash
   git clone https://github.com/OpenGrow-Box/OpenGrowBox-HA.git
   ```
2. Navigate to the cloned directory:
   ```bash
   cd OpenGrowBox-HA
   ```
3. Copy the directory to /usr/share/hassio/homeassitant/custom_components

---

## Configuration

### Basic Setup

1. Add the OpenGrowBox integration to your custom components directory and restart HA
2. Add the Integration.
3. Create your Rooms and enjoy a full auotmated dynamic Grow Enviorment
4. Move the devices to that created Room

### Device Configuration

Use the Home Assistant dashboard to add and manage devices:
- **Sensors**: Temperature, humidity, CO₂, etc.
- **Actuators**: Lights, fans, pumps, etc.

---

## Example Automations


## Roadmap
- Add support for advanced nutrient scheduling.
- Expand compatibility with third-party grow devices.
- Develop a mobile-friendly dashboard for on-the-go monitoring.

---

## Contributing
Contributions are welcome! Please fork the repository, create a new branch, and submit a pull request. Ensure your code adheres to the repository's coding standards.

---

## Support
For issues and feature requests, please open an issue on [GitHub](https://github.com/OpenGrow-Box/OpenGrowBox-HA/issues).


## 📝 License
This project is licensed under the [OGBCL license](LICENSE).
Additional premium features are only provided to paying customers and are not part of this project. They are subject to a separate proprietary license.


## Star History
[![Star History Chart](https://api.star-history.com/svg?repos=OpenGrow-Box/OpenGrowBox-HA&type=Date)](https://www.star-history.com/#OpenGrow-Box/OpenGrowBox-HA&Date)