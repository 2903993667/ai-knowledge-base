"""Dynamic skill loader — scans skills/ directory and loads Python parse modules."""

import os
import sys
import json
import importlib.util
from pathlib import Path

SKILLS_DIR = Path(__file__).resolve().parent.parent / "skills"
BUILTIN_DIR = SKILLS_DIR / "builtin"
LEARNED_DIR = SKILLS_DIR / "learned"


def _load_module(module_id, file_path):
    """Load a Python module from file path."""
    spec = importlib.util.spec_from_file_location(module_id, str(file_path))
    if spec is None or spec.loader is None:
        return None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_id] = mod
    try:
        spec.loader.exec_module(mod)
        return mod
    except Exception as e:
        print(f"Failed to load skill {module_id}: {e}")
        return None


def discover_skills():
    """Discover all skill modules from builtin/ and learned/ directories."""
    skills = []
    for source, d in [("builtin", BUILTIN_DIR), ("learned", LEARNED_DIR)]:
        if not d.exists():
            continue
        for f in d.glob("*.py"):
            if f.name.startswith("_"):
                continue
            module_id = f"skills.{source}.{f.stem}"
            mod = _load_module(module_id, f)
            if mod and hasattr(mod, "SKILL"):
                info = mod.SKILL.copy()
                info["id"] = f"{source}_{f.stem}"
                info["source"] = source
                info["path"] = str(f)
                info["_module"] = mod
                skills.append(info)
    return skills


async def parse_with_skill(skill_info, file_path):
    """Parse a file using the given skill's parse function."""
    mod = skill_info.get("_module")
    if mod is None:
        mod = _load_module(skill_info["id"], skill_info["path"])
    if mod is None or not hasattr(mod, "parse"):
        raise ValueError(f"Skill {skill_info.get('name', skill_info.get('id'))} has no parse() function")
    if hasattr(mod.parse, "__code__"):
        import inspect
        params = inspect.signature(mod.parse).parameters
        if any(p.kind == inspect.Parameter.VAR_KEYWORD or p.name == "file_path" for p in params.values()):
            return await mod.parse(file_path=str(file_path))
        elif any(p.name == "data" for p in params.values()):
            with open(file_path, "rb") as f:
                data = f.read()
            return await mod.parse(data=data)
        else:
            return await mod.parse(str(file_path))
    return await mod.parse(str(file_path))


def save_learned_skill(code, skill_name, extensions):
    """Save AI-generated skill code to the learned directory."""
    LEARNED_DIR.mkdir(parents=True, exist_ok=True)
    filename = f"{skill_name.replace(' ', '_').lower()}.py"
    filepath = LEARNED_DIR / filename
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(code)
    return str(filepath)
