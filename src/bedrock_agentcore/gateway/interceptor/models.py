"""Typed models for the Lambda interceptor envelope contract.

The gateway invokes an interceptor with an input envelope and expects a
specific output envelope back. These models parse the input into typed objects
and build a guaranteed-valid output, so handler authors never construct the
wire shape (and its mandatory ``interceptorOutputVersion``) by hand.
"""

from typing import Any, Dict, Literal, Optional

from pydantic import BaseModel, Field, model_validator

from bedrock_agentcore.gateway.interceptor.version import INTERCEPTOR_INPUT_VERSION, INTERCEPTOR_OUTPUT_VERSION

InterceptorPoint = Literal["REQUEST", "RESPONSE"]


class GatewayRequest(BaseModel):
    """The inbound request as seen by the gateway.

    Attributes:
        path: Request path.
        http_method: HTTP method (aliased from ``httpMethod``).
        headers: Request headers. Empty when ``passRequestHeaders`` is disabled.
        body: Request body (object or scalar).
    """

    path: Optional[str] = None
    http_method: Optional[str] = Field(default=None, alias="httpMethod")
    headers: Dict[str, Any] = Field(default_factory=dict)
    body: Any = None

    model_config = {"populate_by_name": True}


class GatewayResponse(BaseModel):
    """The response produced by the gateway target.

    Attributes:
        status_code: HTTP status code (aliased from ``statusCode``).
        headers: Response headers.
        body: Response body (object or scalar).
    """

    status_code: int = Field(default=200, alias="statusCode")
    headers: Dict[str, Any] = Field(default_factory=dict)
    body: Any = None

    model_config = {"populate_by_name": True}


class InterceptorEvent(BaseModel):
    """Parsed interceptor input.

    Attributes:
        point: ``"REQUEST"`` (before the target) or ``"RESPONSE"`` (after it).
            Derived from which envelope keys are present.
        request: The inbound request. Present at REQUEST and RESPONSE points.
        response: The target response. Present only at the RESPONSE point.
        invocation_index: Streaming chunk index (aliased from ``invocationIndex``).
        raw: The unparsed Lambda event, for fields not surfaced as typed fields.
    """

    point: InterceptorPoint
    request: Optional[GatewayRequest] = None
    response: Optional[GatewayResponse] = None
    invocation_index: Optional[int] = Field(default=None, alias="invocationIndex")
    raw: Dict[str, Any] = Field(default_factory=dict)

    model_config = {"populate_by_name": True}

    @classmethod
    def from_lambda_event(cls, event: Dict[str, Any]) -> "InterceptorEvent":
        """Parse a raw Lambda event into a typed ``InterceptorEvent``.

        The point is RESPONSE when the envelope carries ``gatewayResponse``,
        otherwise REQUEST. RESPONSE events also carry ``gatewayRequest``, so
        ``request`` is populated at both points when present.
        """
        mcp = event.get("mcp") or {}
        raw_request = mcp.get("gatewayRequest")
        raw_response = mcp.get("gatewayResponse")
        point: InterceptorPoint = "RESPONSE" if raw_response is not None else "REQUEST"

        return cls(
            point=point,
            request=GatewayRequest.model_validate(raw_request) if raw_request is not None else None,
            response=GatewayResponse.model_validate(raw_response) if raw_response is not None else None,
            invocationIndex=mcp.get("invocationIndex"),
            raw=event,
        )

    @classmethod
    def sample_request(
        cls,
        *,
        headers: Optional[Dict[str, Any]] = None,
        body: Any = None,
        path: Optional[str] = None,
        http_method: Optional[str] = None,
    ) -> "InterceptorEvent":
        """Build a REQUEST-point event for unit-testing a handler.

        Example::

            event = InterceptorEvent.sample_request(headers={"authorization": "Bearer x"})
            result = handler(event.raw)
        """
        request = GatewayRequest(path=path, httpMethod=http_method, headers=headers or {}, body=body)
        return cls(
            point="REQUEST",
            request=request,
            raw={
                "interceptorInputVersion": INTERCEPTOR_INPUT_VERSION,
                "mcp": {"gatewayRequest": request.model_dump(by_alias=True)},
            },
        )

    @classmethod
    def sample_response(
        cls,
        *,
        status_code: int = 200,
        headers: Optional[Dict[str, Any]] = None,
        body: Any = None,
        request_headers: Optional[Dict[str, Any]] = None,
    ) -> "InterceptorEvent":
        """Build a RESPONSE-point event for unit-testing a handler.

        RESPONSE events carry both the target response and the original
        request, so ``request_headers`` seeds the accompanying request.
        """
        request = GatewayRequest(headers=request_headers or {})
        response = GatewayResponse(statusCode=status_code, headers=headers or {}, body=body)
        return cls(
            point="RESPONSE",
            request=request,
            response=response,
            raw={
                "interceptorInputVersion": INTERCEPTOR_INPUT_VERSION,
                "mcp": {
                    "gatewayRequest": request.model_dump(by_alias=True),
                    "gatewayResponse": response.model_dump(by_alias=True),
                },
            },
        )


class InterceptorResponse(BaseModel):
    """The interceptor's output. Use the constructors rather than building it directly.

    Exactly one of ``transformed_request`` / ``transformed_response`` is set.
    ``to_envelope`` serializes to the wire shape with the mandatory version.
    """

    transformed_request: Optional[Dict[str, Any]] = None
    transformed_response: Optional[Dict[str, Any]] = None

    @model_validator(mode="after")
    def _exactly_one(self) -> "InterceptorResponse":
        """Enforce the envelope invariant: exactly one transform is set.

        The constructors always honor this, but a hand-built
        ``InterceptorResponse()`` (neither field) or one with both fields would
        serialize to a no-op or ambiguous envelope the gateway mishandles.
        Reject it here so the decorator's exception path fails closed (500).
        """
        has_request = self.transformed_request is not None
        has_response = self.transformed_response is not None
        if has_request == has_response:
            raise ValueError(
                "InterceptorResponse must set exactly one of transformed_request or "
                "transformed_response. Use InterceptorResponse.pass_through / "
                "transform_request / transform_response / deny instead of constructing it directly."
            )
        return self

    @classmethod
    def pass_through(cls, event: InterceptorEvent) -> "InterceptorResponse":
        """Forward the request/response unchanged.

        At REQUEST, echoes the request; at RESPONSE, echoes the response.
        """
        if event.point == "RESPONSE":
            resp = event.response or GatewayResponse()
            return cls(
                transformed_response={
                    "statusCode": resp.status_code,
                    "headers": resp.headers,
                    "body": resp.body,
                }
            )
        req = event.request or GatewayRequest()
        return cls(transformed_request={"headers": req.headers, "body": req.body})

    @classmethod
    def transform_request(cls, headers: Dict[str, Any], body: Any) -> "InterceptorResponse":
        """Continue to the target with a modified request (REQUEST point)."""
        return cls(transformed_request={"headers": headers, "body": body})

    @classmethod
    def transform_response(cls, status_code: int, headers: Dict[str, Any], body: Any) -> "InterceptorResponse":
        """Return a modified response to the caller (RESPONSE point, or a REQUEST-point short-circuit)."""
        return cls(transformed_response={"statusCode": status_code, "headers": headers, "body": body})

    @classmethod
    def deny(cls, status_code: int, body: Any, headers: Optional[Dict[str, Any]] = None) -> "InterceptorResponse":
        """Short-circuit with an error response (e.g. a 403 from an auth check)."""
        return cls(
            transformed_response={
                "statusCode": status_code,
                "headers": headers if headers is not None else {"Content-Type": "application/json"},
                "body": body,
            }
        )

    def to_envelope(self) -> Dict[str, Any]:
        """Serialize to the gateway's expected output envelope.

        Always stamps ``interceptorOutputVersion``; omitting it makes the
        gateway silently ignore the interceptor.
        """
        mcp: Dict[str, Any] = {}
        if self.transformed_request is not None:
            mcp["transformedGatewayRequest"] = self.transformed_request
        if self.transformed_response is not None:
            mcp["transformedGatewayResponse"] = self.transformed_response
        return {"interceptorOutputVersion": INTERCEPTOR_OUTPUT_VERSION, "mcp": mcp}
