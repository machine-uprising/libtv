"""Service entry point — thin shim; real logic lives in resources/lib/libtv."""
import os
import sys

import xbmcaddon
import xbmcvfs

sys.path.insert(0, os.path.join(
    xbmcvfs.translatePath(xbmcaddon.Addon().getAddonInfo("path")), "resources", "lib"
))

from libtv import daemon  # noqa: E402

if __name__ == "__main__":
    daemon.run()
