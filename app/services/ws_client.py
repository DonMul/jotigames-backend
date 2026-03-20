import logging
import json
import importlib
from typing import Any, Optional

from app.config import get_settings

logger = logging.getLogger(__name__)


class WsEventPublisher:
    def __init__(self) -> None:
        self._settings = get_settings()

    @property
    def is_enabled(self) -> bool:
        return bool(self._settings.ws_socket_endpoint and self._settings.backend_to_ws_api_key)

    def publish(
        self,
        event: str,
        payload: dict[str, Any],
        channels: Optional[list[str]] = None,
    ) -> None:
        if not self.is_enabled:
            return

        outgoing = {
            "command": "core.publish",
            "apiKey": str(self._settings.backend_to_ws_api_key),
            "event": event,
            "payload": payload,
            "channels": [str(channel).strip() for channel in (channels or []) if str(channel).strip()]
        }

        try:
            websocket_module = importlib.import_module("websocket")
            create_connection = websocket_module.create_connection

            ws_url = str(self._settings.ws_socket_endpoint)
            socket = create_connection(ws_url, timeout=3)
            try:
                socket.send(json.dumps(outgoing))
            finally:
                socket.close()
        except Exception:
            logger.exception("Failed to publish WS event '%s'", event)
