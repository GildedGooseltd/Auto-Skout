import os
from pathlib import Path
from typing import Optional
import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"
PROFILES_DIR = ROOT / "profiles"


def active_profile_id() -> str:
    return os.environ.get("SKOUT_PROFILE", "gardner-farm")


def profile_dir(profile_id: Optional[str] = None) -> Path:
    pid = profile_id or active_profile_id()
    path = PROFILES_DIR / pid
    if not path.is_dir():
        raise FileNotFoundError(f"Profile not found: {pid} (set SKOUT_PROFILE)")
    return path


def load_yaml_path(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_platform_file(name: str) -> dict:
    return load_yaml_path(CONFIG_DIR / name)


def load_profile_file(name: str, profile_id: Optional[str] = None) -> dict:
    return load_yaml_path(profile_dir(profile_id) / name)


def load_all(profile_id: Optional[str] = None) -> dict:
    platform = load_platform_file("platform.yaml")
    scoring_defaults = load_platform_file("scoring.yaml")
    profile_scoring = load_profile_file("scoring.yaml", profile_id)

    return {
        "platform": platform,
        "profile_meta": load_profile_file("profile.yaml", profile_id),
        "profile": load_profile_file("profile.yaml", profile_id),
        "search": load_profile_file("search_criteria.yaml", profile_id),
        "scoring": {**scoring_defaults, **profile_scoring},
        "platforms": load_profile_file("sources.yaml", profile_id),
        "travel": load_profile_file("travel_calendar.yaml", profile_id),
        "routes": load_profile_file("trip_routes.yaml", profile_id),
        "facebook_groups": load_profile_file("facebook_groups.yaml", profile_id),
        "categories": load_platform_file("categories.yaml"),
        "deploy": platform.get("deploy", load_platform_file("deploy.yaml")),
    }


def list_profiles() -> list[str]:
    return [
        p.name
        for p in PROFILES_DIR.iterdir()
        if p.is_dir() and not p.name.startswith("_")
    ]
