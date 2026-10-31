import importlib
import importlib.util
import pkgutil

import app.features as features_pkg


def import_all_models() -> list[str]:
    imported: list[str] = []
    for info in sorted(pkgutil.iter_modules(features_pkg.__path__), key=lambda m: m.name):
        if not info.ispkg:
            continue
        module_name = f"{features_pkg.__name__}.{info.name}.models"
        if importlib.util.find_spec(module_name) is None:
            continue
        importlib.import_module(module_name)
        imported.append(info.name)
    return imported
