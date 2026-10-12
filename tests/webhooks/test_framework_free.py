import subprocess
import sys


def test_importing_webhook_modules_does_not_load_a_web_framework() -> None:
    code = (
        "import sys\n"
        "import app.webhooks.auth, app.webhooks.github, app.webhooks.gitlab\n"
        "import app.webhooks.headers, app.webhooks.payload\n"
        "loaded = sorted(m for m in sys.modules if m.split('.')[0] in ('fastapi', 'starlette'))\n"
        "print(loaded, file=sys.stderr)\n"
        "sys.exit(1 if loaded else 0)\n"
    )

    result = subprocess.run(
        [sys.executable, "-c", code], check=False, capture_output=True, text=True
    )

    assert result.returncode == 0, result.stderr
