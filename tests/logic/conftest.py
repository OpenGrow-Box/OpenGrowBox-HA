from __future__ import annotations

import sys
import types
from pathlib import Path


def _bootstrap_opengrowbox_namespace():
    """Allow importing OGB submodules without executing integration __init__.py."""
    repo_root = Path(__file__).resolve().parents[2]
    custom_components_dir = repo_root / "custom_components"
    ogb_dir = custom_components_dir / "opengrowbox"

    cc_module = sys.modules.get("custom_components")
    if cc_module is None:
        cc_module = types.ModuleType("custom_components")
        cc_module.__path__ = [str(custom_components_dir)]
        sys.modules["custom_components"] = cc_module

    ogb_module = sys.modules.get("custom_components.opengrowbox")
    if ogb_module is None:
        ogb_module = types.ModuleType("custom_components.opengrowbox")
        ogb_module.__path__ = [str(ogb_dir)]
        sys.modules["custom_components.opengrowbox"] = ogb_module


_bootstrap_opengrowbox_namespace()
