import uuid

from slowapi import Limiter
from slowapi.util import get_remote_address


def _rate_limit_key(request):
    # Dev: all traffic shares one Docker-internal IP — return a UUID so limits
    # never fire during local development or player simulation.
    # Stress test: if STRESS_TEST_KEY is set and the request carries a matching
    # X-Stress-Key header, treat each request as a unique IP so the load test
    # is never blocked by rate limiting.
    from app.config import settings

    if settings.is_development:
        return str(uuid.uuid4())
    if settings.STRESS_TEST_KEY:
        if request.headers.get("X-Stress-Key") == settings.STRESS_TEST_KEY:
            return str(uuid.uuid4())
    return get_remote_address(request)


limiter = Limiter(key_func=_rate_limit_key)
