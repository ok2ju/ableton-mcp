"""ControlSurface glue: Live's timer calls update_display(), which ticks the bridge."""

import sys

from _Framework.ControlSurface import ControlSurface

from .bridge import Bridge
from .ops import OPS


class AbletonMCP(ControlSurface):
    def __init__(self, c_instance):
        super().__init__(c_instance)
        self._bridge = Bridge(self.song, self.application, OPS, log=self.log_message)
        port = self._bridge.start()
        self.log_message(f"AbletonMCP listening on 127.0.0.1:{port} (Python {sys.version.split()[0]})")

    def update_display(self):
        super().update_display()
        self._bridge.tick()

    def disconnect(self):
        self._bridge.close()
        super().disconnect()
