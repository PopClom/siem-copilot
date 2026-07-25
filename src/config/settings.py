"""
config/settings.py
------------------
Loads and validates the YAML configuration file using Pydantic v2.
All other components receive a Settings instance — never raw dicts.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Literal, Optional

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator


# ---------------------------------------------------------------------------
# Sub-models
# ---------------------------------------------------------------------------

class EmbeddingConfig(BaseModel):
    model: str = "BAAI/bge-small-en-v1.5"
    device: Literal["cpu", "cuda", "mps"] = "cpu"
    batch_size: int = Field(512, alias="batch-size")

    model_config = {"populate_by_name": True}


class VectorDBConfig(BaseModel):
    type: Literal["qdrant"] = "qdrant"
    host: str = "localhost"
    port: int = 6333
    collection: str = "siem_logs"


class LLMConfig(BaseModel):
    model: str = "claude-sonnet-4-20250514"
    api_key: str = Field(..., alias="api-key")
    temperature: float = 0.0

    model_config = {"populate_by_name": True}

    @field_validator("api_key", mode="before")
    @classmethod
    def resolve_env_var(cls, v: str) -> str:
        """If the value looks like an env-var name, resolve it at load time."""
        if re.match(r"^[A-Z][A-Z0-9_]+$", str(v)):
            resolved = os.environ.get(v)
            if resolved:
                return resolved
        return v


class GroupingConfig(BaseModel):
    host: bool = True
    user: bool = False
    time_window: str = Field("1m", alias="time-window")
    max_events_per_chunk: int | None = Field(None, alias="max-events-per-chunk")
    overlap_ratio: float = Field(0.5, alias="overlap-ratio")

    model_config = {"populate_by_name": True}

    @field_validator("time_window")
    @classmethod
    def parse_duration(cls, v: str) -> str:
        if not re.match(r"^\d+[smh]$", v):
            raise ValueError(
                f"Invalid time-window '{v}'. Use format like '1m', '30s', '1h'."
            )
        return v

    @field_validator("max_events_per_chunk")
    @classmethod
    def validate_max_events_per_chunk(cls, v: int | None) -> int | None:
        if v is not None and v <= 0:
            raise ValueError("max_events_per_chunk must be positive or None")
        return v

    @field_validator("overlap_ratio")
    @classmethod
    def validate_overlap_ratio(cls, v: float) -> float:
        if not 0 <= v <= 0.75:
            raise ValueError("overlap_ratio must be between 0 and 0.75")
        return v

    def to_seconds(self) -> int:
        """Return the time window duration in seconds."""
        unit_map = {"s": 1, "m": 60, "h": 3600}
        return int(self.time_window[:-1]) * unit_map[self.time_window[-1]]


# ---------------------------------------------------------------------------
# Source sub-models
# ---------------------------------------------------------------------------

class SSLConfig(BaseModel):
    verify: bool = True


class AuthConfig(BaseModel):
    username: str
    password: str


class ConnectionConfig(BaseModel):
    host: str
    port: int
    index: str
    auth: AuthConfig
    ssl: SSLConfig = SSLConfig()


class PollingConfig(BaseModel):
    enabled: bool = False
    interval: str = "30s"


class QueryConfig(BaseModel):
    agent_name: Optional[str] = Field(None, alias="agent-name")
    min_rule_level: Optional[int] = Field(None, alias="min-rule-level")

    model_config = {"populate_by_name": True}


class SourceConfig(BaseModel):
    name: str
    enabled: bool = True
    type: Literal["file", "opensearch"]
    input_format: str = Field(..., alias="input-format")
    semantic_type: str = Field(..., alias="semantic-type")

    # File source fields
    path: Optional[str] = None
    recursive: bool = False
    file_patterns: list[str] = Field(default_factory=lambda: ["*.log"], alias="file-patterns")

    # OpenSearch source fields
    connection: Optional[ConnectionConfig] = None
    polling: Optional[PollingConfig] = None
    query: Optional[QueryConfig] = None

    model_config = {"populate_by_name": True}

    @model_validator(mode="after")
    def validate_source_fields(self) -> SourceConfig:
        if self.type == "file" and not self.path:
            raise ValueError(f"Source '{self.name}': 'path' is required for file sources.")
        if self.type == "opensearch" and not self.connection:
            raise ValueError(f"Source '{self.name}': 'connection' is required for opensearch sources.")
        return self


# ---------------------------------------------------------------------------
# RAG config
# ---------------------------------------------------------------------------

class RagConfig(BaseModel):
    use_hyde: bool = Field(False, alias="use-hyde")
    top_k: int = Field(8, alias="top-k")
    score_threshold: float = Field(0.30, alias="score-threshold")

    model_config = {"populate_by_name": True}


# ---------------------------------------------------------------------------
# Root settings
# ---------------------------------------------------------------------------

class Settings(BaseModel):
    embedding: EmbeddingConfig
    vector_db: VectorDBConfig = Field(..., alias="vector-db")
    llm: LLMConfig
    grouping: GroupingConfig = GroupingConfig()
    rag: RagConfig = Field(default_factory=RagConfig)
    sources: list[SourceConfig] = Field(default_factory=list)

    model_config = {"populate_by_name": True}

    @property
    def enabled_sources(self) -> list[SourceConfig]:
        return [s for s in self.sources if s.enabled]


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------

def load_settings(path: str | Path = "config/config.yaml") -> Settings:
    """Parse and validate the YAML config file, returning a Settings object."""
    config_path = Path(path)
    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path.resolve()}")

    with config_path.open("r", encoding="utf-8") as f:
        raw: dict[str, Any] = yaml.safe_load(f)

    return Settings.model_validate(raw)
