import subprocess
import sys

import pytest

PURE_MODULES = (
    "app.ingestion",
    "app.ingestion.events",
    "app.ingestion.ports",
    "app.ingestion.service",
    "app.ingestion.dispatch",
    "app.webhooks.auth",
    "app.webhooks.github",
    "app.webhooks.gitlab",
    "app.webhooks.headers",
    "app.webhooks.payload",
)
ADAPTER_MODULES = (
    "app.repositories.ingestion",
    "app.worker.ingestion_queue",
    "app.composition.ingestion",
    "app.worker.ingestion",
    "app.worker.recovery",
    "app.api.webhooks",
)
FORBIDDEN = ("sqlalchemy", "rq", "redis", "fastapi", "starlette")


def _run(code: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, "-c", code], check=False, capture_output=True, text=True)


def test_pure_modules_load_no_framework_or_infrastructure_package() -> None:
    imports = "\n".join(f"import {name}" for name in PURE_MODULES)
    code = (
        "import sys\n"
        f"{imports}\n"
        f"forbidden = {FORBIDDEN!r}\n"
        "loaded = sorted(m for m in sys.modules if m.split('.')[0] in forbidden)\n"
        "print(loaded, file=sys.stderr)\n"
        "sys.exit(1 if loaded else 0)\n"
    )

    result = _run(code)

    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("module", ADAPTER_MODULES)
def test_adapter_module_imports_first_in_a_fresh_interpreter(module: str) -> None:
    result = _run(f"import {module}")

    assert result.returncode == 0, result.stderr
