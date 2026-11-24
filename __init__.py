import importlib.util as _importlib_util
import os as _os

from .nodes import NODE_CLASS_MAPPINGS, NODE_DISPLAY_NAME_MAPPINGS
from . import server_api as _server_api

if _os.environ.get("XIAOYUE_DISABLE_PRIVATE_EXTENSIONS") != "1":
    _private_name = f"{__package__}.local_extension"
    if _importlib_util.find_spec(_private_name) is not None:
        from .local_extension import (
            NODE_CLASS_MAPPINGS as _PRIVATE_NODE_CLASS_MAPPINGS,
            NODE_DISPLAY_NAME_MAPPINGS as _PRIVATE_NODE_DISPLAY_NAME_MAPPINGS,
        )
        NODE_CLASS_MAPPINGS.update(_PRIVATE_NODE_CLASS_MAPPINGS)
        NODE_DISPLAY_NAME_MAPPINGS.update(_PRIVATE_NODE_DISPLAY_NAME_MAPPINGS)

__version__ = "0.2.9"

WEB_DIRECTORY = "./web"

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS", "WEB_DIRECTORY"]
