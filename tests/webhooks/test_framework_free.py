import subprocess
import sys


def test_importing_webhook_modules_does_not_load_a_web_framework():
    code = (
        "import sys\n"
        "import app.webhooks.auth, app.webhooks.github, app.webhooks.gitlab\n"
        "import app.webhooks.headers, app.webhooks.payload\n"
        "sys.exit(1 if any(m.split('.')[0] in ('fastapi', 'starlette') for m in sys.modules) else 0)\n"
    )

    result = subprocess.run([sys.executable, "-c", code], check=False)

    assert result.returncode == 0
