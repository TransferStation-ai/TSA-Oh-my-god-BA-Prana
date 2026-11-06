from __future__ import annotations

import ast
import hashlib
import importlib
import logging
from pathlib import Path
import sys
import threading
from types import ModuleType

logger = logging.getLogger(__name__)


class SkillHotReloader:
    def __init__(
        self,
        package_name: str = "skills",
        package_root: Path | None = None,
    ) -> None:
        self.package_name = package_name
        self.package_root = Path(
            package_root or Path(__file__).resolve().parent
        ).resolve()
        self.enabled = not getattr(sys, "frozen", False)
        self._lock = threading.RLock()
        self._fingerprints = self._scan()

    def reload_if_changed(self) -> bool:
        if not self.enabled:
            return False
        with self._lock:
            current = self._scan()
            if current == self._fingerprints:
                return False
            deleted = self._fingerprints.keys() - current.keys()
            if deleted:
                paths = ", ".join(sorted(path.name for path in deleted))
                raise RuntimeError(
                    f"Skills 源文件已删除，需重启应用后加载：{paths}"
                )
            changed = current.keys() - self._fingerprints.keys()
            changed.update(
                path
                for path in current.keys() & self._fingerprints.keys()
                if current[path] != self._fingerprints[path]
            )
            self._validate_syntax(changed)
            self._reload_loaded_modules()
            self._fingerprints = current
            logger.info(
                "已热更新 Skills 源码：%s",
                ", ".join(sorted(path.name for path in changed)),
            )
            return True

    def _scan(self) -> dict[Path, str]:
        fingerprints: dict[Path, str] = {}
        for path in self.package_root.rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            fingerprints[path.resolve()] = hashlib.sha256(
                path.read_bytes()
            ).hexdigest()
        return fingerprints

    @staticmethod
    def _validate_syntax(paths: set[Path]) -> None:
        for path in paths:
            try:
                source = path.read_text(encoding="utf-8")
                compile(source, str(path), "exec")
            except (OSError, UnicodeError, SyntaxError) as error:
                raise RuntimeError(
                    f"Skills 热更新未执行，源文件无法加载：{path}"
                ) from error

    def _reload_loaded_modules(self) -> None:
        importlib.invalidate_caches()
        modules = self._loaded_modules()
        dependencies = {
            name: self._dependencies(name, module, modules)
            for name, module in modules.items()
        }
        order: list[str] = []
        visited: set[str] = set()
        visiting: set[str] = set()

        def visit(name: str) -> None:
            if name in visited or name in visiting:
                return
            visiting.add(name)
            for dependency in sorted(dependencies[name]):
                visit(dependency)
            visiting.remove(name)
            visited.add(name)
            order.append(name)

        for name in sorted(modules):
            visit(name)
        for name in order:
            cached = getattr(modules[name], "__cached__", None)
            if cached:
                cached_path = Path(cached).resolve()
                try:
                    cached_path.relative_to(self.package_root)
                except ValueError:
                    pass
                else:
                    if cached_path.suffix == ".pyc":
                        cached_path.unlink(missing_ok=True)
            importlib.reload(modules[name])

    def _loaded_modules(self) -> dict[str, ModuleType]:
        modules: dict[str, ModuleType] = {}
        for name, module in tuple(sys.modules.items()):
            if not isinstance(module, ModuleType) or not (
                name == self.package_name
                or name.startswith(self.package_name + ".")
            ):
                continue
            source = getattr(module, "__file__", None)
            if not source:
                continue
            try:
                Path(source).resolve().relative_to(self.package_root)
            except (OSError, ValueError):
                continue
            modules[name] = module
        return modules

    def _dependencies(
        self,
        module_name: str,
        module: ModuleType,
        loaded: dict[str, ModuleType],
    ) -> set[str]:
        source_path = Path(module.__file__ or "")
        try:
            source = source_path.read_text(encoding="utf-8")
            tree = ast.parse(source)
        except (OSError, UnicodeError, SyntaxError):
            return set()

        package = (
            module_name
            if source_path.name == "__init__.py"
            else module_name.rpartition(".")[0]
        )
        dependencies: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                dependencies.update(
                    alias.name
                    for alias in node.names
                    if alias.name in loaded and alias.name != module_name
                )
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    parts = package.split(".")
                    base = parts[: len(parts) - node.level + 1]
                    if node.module:
                        imported = ".".join(base + node.module.split("."))
                        if imported in loaded and imported != module_name:
                            dependencies.add(imported)
                    else:
                        for alias in node.names:
                            imported = ".".join(base + [alias.name])
                            if imported in loaded and imported != module_name:
                                dependencies.add(imported)
                elif node.module:
                    imported = node.module
                    if imported in loaded and imported != module_name:
                        dependencies.add(imported)
        return dependencies
