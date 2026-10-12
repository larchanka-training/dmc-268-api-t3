class WebhookError(Exception):
    """Base class for webhook rejection. Messages never contain secrets or payload data."""


class WebhookAuthError(WebhookError):
    """Authentication failed or the endpoint is not (correctly) configured."""


class WebhookPayloadError(WebhookError):
    """An authenticated, supported delivery has an invalid payload."""
