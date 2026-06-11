"""Decorator that adapts a typed interceptor function into a Lambda handler."""

import functools
import logging

from bedrock_agentcore.gateway.interceptor.models import InterceptorEvent, InterceptorResponse
from bedrock_agentcore.gateway.interceptor.version import INTERCEPTOR_INPUT_VERSION

logger = logging.getLogger(__name__)


def interceptor():
    """Decorator that wraps a typed interceptor function as a Lambda handler.

    The decorated function receives an ``InterceptorEvent`` and the Lambda
    ``context``, and returns an ``InterceptorResponse``. The decorator parses
    the raw Lambda event, serializes the response into the envelope the gateway
    expects (always stamping ``interceptorOutputVersion``), and converts any
    uncaught exception into a safe 500 response so the gateway does not retry
    and double-invoke the interceptor.

    Must be called with parentheses: ``@interceptor()``.

    Example::

        @interceptor()
        def lambda_handler(event: InterceptorEvent, context) -> InterceptorResponse:
            if not event.request.headers.get("authorization"):
                return InterceptorResponse.deny(403, {"error": "forbidden"})
            return InterceptorResponse.pass_through(event)
    """

    def decorator(fn):
        @functools.wraps(fn)
        def lambda_handler(event, context=None):
            logger.debug("Raw interceptor event: %s", event)

            try:
                input_version = event.get("interceptorInputVersion")
                if input_version is not None and input_version != INTERCEPTOR_INPUT_VERSION:
                    logger.warning(
                        "Interceptor input version %s does not match expected %s; parsing best-effort.",
                        input_version,
                        INTERCEPTOR_INPUT_VERSION,
                    )

                interceptor_event = InterceptorEvent.from_lambda_event(event)
                result = fn(interceptor_event, context)

                if not isinstance(result, InterceptorResponse):
                    raise TypeError(f"Interceptor must return an InterceptorResponse, got {type(result).__name__}")

                return result.to_envelope()
            except Exception:
                # Never propagate: a thrown handler makes the gateway retry and
                # invoke the interceptor twice. Fail closed with a 500 so a
                # broken REQUEST interceptor blocks the call rather than letting
                # unauthorized traffic through.
                logger.exception("Interceptor handler raised; returning a 500 response.")
                return InterceptorResponse.deny(500, {"error": "interceptor_error"}).to_envelope()

        lambda_handler.unwrapped = fn
        return lambda_handler

    return decorator
