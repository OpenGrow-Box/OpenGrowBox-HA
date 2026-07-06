DOMAIN = "opengrowbox"
VERSION = "1.4.2"
URL_BASE = "/ogb"
CONF_AUTO_CONFIGURE_HA = "auto_configure_ha"
DEFAULT_AUTO_CONFIGURE_HA = False
FRONTEND_EXTRA_MODULE_URL = "/local/opengrowbox/ogb_icons.js"

# Self-update (post-HACS) settings
CONF_ENABLE_AUTO_UPDATE = "enable_auto_update"
DEFAULT_ENABLE_AUTO_UPDATE = True
GITHUB_REPO = "OpenGrow-Box/OpenGrowBox-HA"
GITHUB_RELEASES_LATEST_API = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
GITHUB_RELEASES_TAG_API = f"https://api.github.com/repos/{GITHUB_REPO}/releases/tags/v{{version}}"
RELEASE_ASSET_NAME = "opengrowbox.zip"

# V1 API WebSocket endpoint
# Development: ws://10.1.1.8:5000
# Production:  wss://prem.opengrowbox.net
#PREM_WS_API = "ws://10.1.1.8:5000"
PREM_WS_API = "wss://prem.opengrowbox.net"
