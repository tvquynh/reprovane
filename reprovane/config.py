"""Optional private configuration file (config.json).

Precedence for every setting: CLI argument > config.json > code default. For the OpenAlex API key
only, the legacy OPENALEX_API_KEY environment variable is still honoured after config.json
(deprecated, kept so existing v3.0.x setups do not break).

config.json holds the API key, so it is validated strictly (unknown sections/keys, wrong types and
values outside a fixed list are errors) and no error message ever echoes a value, or an unknown key
name, from the file.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from .openalex import SEARCH_FIELDS

CONFIG_FILENAME = "config.json"

# section -> key -> allowed JSON types
SCHEMA: Dict[str, Dict[str, Tuple[type, ...]]] = {
    "openalex": {"api_key": (str,), "email": (str,)},
    "scimago": {"manifest_url": (str,), "cache_dir": (str,)},
    "run": {"from_year": (int,), "to_year": (int,), "require_abstract": (bool,), "max_per_query": (int, type(None)),
            "search_field": (str,)},
    # defaults for run and enrich: the venue list (--venue-tiers) and the tiers it admits (--admit-tier)
    "screening": {"venue_tiers": (str,), "admit_tiers": (list,)},
}
# (section, key) -> allowed string values; an empty string still means "use the default"
CHOICES: Dict[Tuple[str, str], Tuple[str, ...]] = {("run", "search_field"): SEARCH_FIELDS}
_TYPE_NAMES = {str: "a string", int: "an integer", bool: "true/false", type(None): "null", list: "a list of strings"}


class ConfigError(ValueError):
    pass


@dataclass
class Config:
    path: Optional[Path] = None
    data: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    def get(self, section: str, key: str) -> Any:
        """Configured value, or None when the file, section or key is absent or the string is empty."""
        value = (self.data.get(section) or {}).get(key)
        return None if value == "" else value

    @property
    def label(self) -> str:
        """Name used in metadata/console as the source of a setting (never the value)."""
        return self.path.name if self.path else ""


def _type_ok(value: Any, allowed: Tuple[type, ...]) -> bool:
    if isinstance(value, bool):  # bool is an int subclass: only accept it where bool is allowed
        return bool in allowed
    return isinstance(value, allowed)


def load_config(explicit_path: Optional[Path], default_dir: Path) -> Config:
    """Load `explicit_path`, else `<default_dir>/config.json` if it exists, else an empty Config."""
    if explicit_path is not None:
        path = Path(explicit_path)
        if not path.is_file():
            raise ConfigError(f"Config file not found: {path}")
    else:
        path = Path(default_dir) / CONFIG_FILENAME
        if not path.is_file():
            return Config()
    try:
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{path}: invalid JSON ({exc.msg} at line {exc.lineno}, column {exc.colno})") from None
    except UnicodeDecodeError:
        raise ConfigError(f"{path}: file is not UTF-8 text") from None
    if not isinstance(raw, dict):
        raise ConfigError(f"{path}: the top level must be a JSON object")

    data: Dict[str, Dict[str, Any]] = {}
    for section, values in raw.items():
        if section.startswith("_"):  # comment entries such as "_comment"
            continue
        if section not in SCHEMA:
            raise ConfigError(f"{path}: unknown top-level section (allowed: {', '.join(SCHEMA)}, or names starting with '_')")
        if not isinstance(values, dict):
            raise ConfigError(f"{path}: section '{section}' must be a JSON object")
        clean: Dict[str, Any] = {}
        for key, value in values.items():
            if key.startswith("_"):
                continue
            if key not in SCHEMA[section]:
                raise ConfigError(f"{path}: unknown key in section '{section}' (allowed: {', '.join(SCHEMA[section])})")
            allowed = SCHEMA[section][key]
            if not _type_ok(value, allowed):
                expected = " or ".join(_TYPE_NAMES[t] for t in allowed)
                raise ConfigError(f"{path}: '{section}.{key}' must be {expected}")
            if isinstance(value, list):
                if not all(isinstance(item, str) and item.strip() for item in value):
                    raise ConfigError(f"{path}: '{section}.{key}' must be a list of non-empty strings")
                value = [item.strip() for item in value]
            choices = CHOICES.get((section, key))
            if choices and value.strip() and value.strip() not in choices:
                raise ConfigError(f"{path}: '{section}.{key}' must be one of: {', '.join(choices)}")
            clean[key] = value.strip() if isinstance(value, str) else value
        data[section] = clean

    # Relative paths are relative to the config file, not to the current directory.
    for section, key in (("scimago", "cache_dir"), ("screening", "venue_tiers")):
        value = (data.get(section) or {}).get(key)
        if value:
            resolved = Path(value).expanduser()
            data[section][key] = str(resolved if resolved.is_absolute() else path.parent / resolved)
    return Config(path=path, data=data)
