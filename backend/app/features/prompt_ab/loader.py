"""The four prompt modules of a git ref, loaded beside today's without touching them.

Imports between the four modules are rewired to private names, so an old module binds the old
version of another; every other import (schemas, profiles) binds today's code.
"""

import ast
import hashlib
import importlib
import re
import subprocess
import sys
import types
from collections.abc import Callable
from dataclasses import dataclass
from types import ModuleType

from app.config import REPO_ROOT

PROMPT_PATHS = {
    "topics": "backend/app/features/topics/prompts.py",
    "questions": "backend/app/features/questions/prompts.py",
    "assessment": "backend/app/features/assessment/prompts.py",
    "follow_ups": "backend/app/features/follow_ups/prompts.py",
}
LOAD_ORDER = ("topics", "questions", "assessment", "follow_ups")
PRIVATE_PREFIX = "_prompt_ab_"


class LoaderError(Exception):
    pass


@dataclass(frozen=True)
class PromptModules:
    topics: ModuleType
    questions: ModuleType
    assessment: ModuleType
    follow_ups: ModuleType


Show = Callable[[str, str], str]


def _git(*args: str) -> str:
    try:
        done = subprocess.run(
            ["git", "-C", str(REPO_ROOT), *args],
            capture_output=True,
            check=False,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise LoaderError(f"git {args[0]} failed: {error}") from error
    if done.returncode != 0:
        message = done.stderr.decode("utf-8", "replace").strip()
        raise LoaderError(f"git {' '.join(args)} failed: {message}")
    return done.stdout.decode("utf-8")


def git_show(ref: str, path: str) -> str:
    return _git("show", f"{ref}:{path}")


def resolve_ref(ref: str) -> str:
    return _git("rev-parse", "--verify", f"{ref}^{{commit}}").strip()


def _package_name(ref: str) -> str:
    key = (
        ref.lower()
        if re.fullmatch(r"[0-9a-fA-F]{12,40}", ref)
        else hashlib.sha1(ref.encode("utf-8")).hexdigest()
    )
    return PRIVATE_PREFIX + key[:12]


def _rewire(source: str, package: str) -> ast.Module:
    """Every import of one of the four prompt modules points at its private copy."""
    targets = {f"app.features.{key}.prompts": key for key in PROMPT_PATHS}
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            if node.module in targets:
                node.module = f"{package}.{targets[node.module]}"
            elif node.module.startswith("app.features."):
                feature = node.module.removeprefix("app.features.")
                if feature in PROMPT_PATHS and any(a.name == "prompts" for a in node.names):
                    if len(node.names) != 1:
                        raise LoaderError(f"unsupported import of {feature} prompts")
                    alias = node.names[0]
                    node.module = package
                    node.names = [ast.alias(name=feature, asname=alias.asname or "prompts")]
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in targets:
                    if alias.asname is None:
                        raise LoaderError(f"unsupported import of {alias.name} without a name")
                    alias.name = f"{package}.{targets[alias.name]}"
    return ast.fix_missing_locations(tree)


def load_prompt_modules(ref: str, *, show: Show = git_show) -> PromptModules:
    package = _package_name(ref)
    names = [package, *(f"{package}.{key}" for key in LOAD_ORDER)]
    saved = {name: sys.modules[name] for name in names if name in sys.modules}
    loaded: dict[str, ModuleType] = {}
    try:
        root = types.ModuleType(package)
        root.__path__ = []  # a package, so "package.topics" imports resolve
        sys.modules[package] = root
        for key in LOAD_ORDER:
            path = PROMPT_PATHS[key]
            try:
                source = show(ref, path)
            except Exception as error:
                raise LoaderError(f"{key}: cannot read {path} at {ref}: {error}") from error
            name = f"{package}.{key}"
            module = types.ModuleType(name)
            module.__file__ = f"<{ref}:{path}>"
            sys.modules[name] = module
            try:
                code = compile(_rewire(source, package), module.__file__, "exec")
                exec(code, module.__dict__)
            except LoaderError as error:
                raise LoaderError(f"{key}: {error}") from error
            except Exception as error:
                raise LoaderError(f"{key}: {type(error).__name__}: {error}") from error
            setattr(root, key, module)
            loaded[key] = module
    finally:
        for name in names:
            sys.modules.pop(name, None)
        sys.modules.update(saved)
    return PromptModules(**loaded)


def current_prompt_modules() -> PromptModules:
    return PromptModules(
        **{key: importlib.import_module(f"app.features.{key}.prompts") for key in LOAD_ORDER}
    )
