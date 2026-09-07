"""
Home Assistant REST API Client with TTL caching and resilience.
"""
import json
import time
import urllib.request
import urllib.error
from typing import Optional
from .config import Config


class HomeAssistantClient:
    def __init__(self, config: Config):
        self.config = config
        self._last_state = "unknown"
        self._last_check_time = 0.0
        self._cache_ttl = 2.0

    @property
    def is_enabled(self) -> bool:
        return (
            self.config.enable_home_assistant
            and bool(self.config.ha_url)
            and bool(self.config.ha_token)
            and bool(self.config.ha_entity_id)
        )

    def get_state(self, force_refresh: bool = False) -> str:
        if not self.is_enabled:
            return "always-on"

        now = time.time()
        if not force_refresh and (now - self._last_check_time < self._cache_ttl) and self._last_state != "unknown":
            return self._last_state

        url = f"{self.config.ha_url.rstrip('/')}/api/states/{self.config.ha_entity_id}"
        req = urllib.request.Request(
            url,
            headers={
                "Authorization": f"Bearer {self.config.ha_token}",
                "Content-Type": "application/json",
            },
        )

        try:
            with urllib.request.urlopen(req, timeout=2.0) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                state = str(data.get("state", "unknown")).lower()
                self._last_state = state
                self._last_check_time = now
                return state
        except Exception:
            # If recent cache is available (within 10s), reuse it, otherwise return unknown
            if now - self._last_check_time < 10.0 and self._last_state != "unknown":
                return self._last_state
            self._last_state = "unknown"
            return "unknown"

    def call_service(self, action: str) -> bool:
        """
        action: 'turn_on' or 'turn_off'
        """
        if not self.is_enabled:
            return True

        url = f"{self.config.ha_url.rstrip('/')}/api/services/switch/{action}"
        payload = json.dumps({"entity_id": self.config.ha_entity_id}).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=payload,
            headers={
                "Authorization": f"Bearer {self.config.ha_token}",
                "Content-Type": "application/json",
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=4.0) as resp:
                if resp.status in (200, 201):
                    # Invalidate cache
                    self._last_check_time = 0.0
                    return True
                return False
        except Exception:
            return False

    def turn_on(self) -> bool:
        return self.call_service("turn_on")

    def turn_off(self) -> bool:
        return self.call_service("turn_off")
