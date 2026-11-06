from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class BrowserConfig:
    base_url: str
    username: str
    password: str = field(repr=False)
    timeout_ms: int = 10_000


@dataclass(frozen=True)
class WorkerConfig:
    browser: BrowserConfig
    state_dir: Path
    attempts: int = 3
    headed: bool = False
    screenshots: bool = False
    retry_delay: float = 0.5
