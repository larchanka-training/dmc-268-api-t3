# Reuse the in-process API harness (fail-if-called DB factory, fakeredis, test secrets).
from tests.api.conftest import api  # noqa: F401
