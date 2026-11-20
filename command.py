import time
import uuid
from dataclasses import dataclass, field
from enum import Enum


class Domain(str, Enum):
    TAB = "tab"
    SYSTEM = "system"
    MEDIA = "media"
    QUERY = "query"
    APPS = "apps"
    FILES = "files"
    COMPUTER = "computer"
    NAV = "nav"
    SFX = "sfx"
    CONVO = "convo"


class Risk(int, Enum):
    LOW = 0
    MEDIUM = 1
    HIGH = 2


ACTION_REGISTRY = {
    ("tab", "close"): (Risk.LOW, False),
    ("tab", "new"): (Risk.LOW, False),
    ("tab", "next"): (Risk.LOW, False),
    ("tab", "prev"): (Risk.LOW, False),
    ("tab", "reopen"): (Risk.LOW, False),
    ("tab", "minimize"): (Risk.LOW, False),
    ("tab", "maximize"): (Risk.LOW, False),
    ("tab", "desktop"): (Risk.LOW, False),
    ("tab", "task_view"): (Risk.LOW, False),
    ("system", "volume"): (Risk.LOW, False),
    ("system", "brightness_up"): (Risk.LOW, False),
    ("system", "brightness_down"): (Risk.LOW, False),
    ("media", "play"): (Risk.LOW, False),
    ("media", "pause"): (Risk.LOW, False),
    ("media", "resume"): (Risk.LOW, False),
    ("media", "next"): (Risk.LOW, False),
    ("media", "prev"): (Risk.LOW, False),
    ("media", "stop"): (Risk.LOW, False),
    ("media", "replay"): (Risk.LOW, False),
    ("media", "toggle"): (Risk.LOW, False),
    ("query", "web_search"): (Risk.LOW, False),
    ("query", "open_url"): (Risk.LOW, False),
    ("query", "screenshot"): (Risk.LOW, False),
    ("computer", "click"): (Risk.LOW, False),
    ("computer", "dblclick"): (Risk.LOW, False),
    ("computer", "type"): (Risk.LOW, False),
    ("computer", "press"): (Risk.LOW, False),
    ("computer", "scroll"): (Risk.LOW, False),
    ("computer", "hotkey"): (Risk.LOW, False),
    ("nav", "back"): (Risk.LOW, False),
    ("nav", "refresh"): (Risk.LOW, False),
    ("nav", "home"): (Risk.LOW, False),
    ("nav", "page_down"): (Risk.LOW, False),
    ("nav", "page_up"): (Risk.LOW, False),
    ("nav", "address"): (Risk.LOW, False),
    ("nav", "find"): (Risk.LOW, False),
    ("nav", "bookmark"): (Risk.LOW, False),
    ("nav", "replay"): (Risk.LOW, False),
    ("sfx", "giggle"): (Risk.LOW, False),
    ("apps", "open"): (Risk.LOW, False),
    ("apps", "close"): (Risk.LOW, False),
    ("apps", "switch"): (Risk.LOW, False),
    ("apps", "minimize"): (Risk.LOW, False),
    ("apps", "maximize"): (Risk.LOW, False),
    ("files", "open_folder"): (Risk.LOW, False),
    ("files", "create"): (Risk.MEDIUM, False),
    ("files", "rename"): (Risk.MEDIUM, False),
    ("files", "copy"): (Risk.MEDIUM, False),
    ("files", "move"): (Risk.MEDIUM, False),
    ("files", "delete"): (Risk.HIGH, True),
}


@dataclass
class Command:
    domain: str
    action: str
    target: str | None = None
    params: dict = field(default_factory=dict)
    confidence: float = 1.0
    raw: str = ""
    cmd_id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    created_at: float = field(default_factory=time.time)

    @property
    def registry_entry(self):
        return ACTION_REGISTRY.get((self.domain, self.action))

    @property
    def risk(self):
        entry = self.registry_entry
        return entry[0] if entry else Risk.MEDIUM

    @property
    def requires_confirmation(self):
        entry = self.registry_entry
        return bool(entry and entry[1])

    def validate(self):
        if (self.domain, self.action) not in ACTION_REGISTRY:
            return False, f"action not allowlisted: {self.domain}/{self.action}"
        return True, ""

    def to_dict(self):
        return {
            "domain": self.domain,
            "action": self.action,
            "target": self.target,
            "params": dict(self.params),
            "confidence": self.confidence,
            "raw": self.raw,
            "id": self.cmd_id,
        }


def is_allowed(domain, action):
    return (domain, action) in ACTION_REGISTRY


def build_command(domain, action, target=None, raw="", params=None):
    return Command(domain=domain, action=action, target=target, raw=raw, params=params or {})
