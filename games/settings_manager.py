import os
import json
import logging
from typing import Any, Dict

logger = logging.getLogger(__name__)

SETTINGS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "group_settings.json")

class SettingsManager:
    """Manages persistent group-specific settings."""
    
    def __init__(self):
        self.settings: Dict[str, Dict[str, Any]] = self._load_settings()

    def _load_settings(self) -> Dict[str, Dict[str, Any]]:
        """Load settings from JSON file."""
        if not os.path.exists(SETTINGS_FILE):
            return {}
        try:
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            logger.error(f"Error loading settings: {e}")
            return {}

    def _save_settings(self) -> None:
        """Save settings to JSON file."""
        try:
            with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
                json.dump(self.settings, f, indent=2, ensure_ascii=False)
        except IOError as e:
            logger.error(f"Error saving settings: {e}")

    def get_setting(self, chat_id: int, key: str, default: Any = None) -> Any:
        """Get a setting for a specific chat."""
        chat_id_str = str(chat_id)
        return self.settings.get(chat_id_str, {}).get(key, default)

    def set_setting(self, chat_id: int, key: str, value: Any) -> None:
        """Set a setting for a specific chat and save to disk."""
        chat_id_str = str(chat_id)
        if chat_id_str not in self.settings:
            self.settings[chat_id_str] = {}
        self.settings[chat_id_str][key] = value
        self._save_settings()

# Initialize global settings manager
settings_manager = SettingsManager()
