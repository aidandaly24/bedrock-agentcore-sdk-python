"""Tests for the @interceptor decorator."""

from bedrock_agentcore.gateway.interceptor import (
    InterceptorEvent,
    InterceptorResponse,
    interceptor,
)


def _request_event(headers=None, body=None):
    return {
        "interceptorInputVersion": "1.0",
        "mcp": {"gatewayRequest": {"headers": headers or {}, "body": body}},
    }


def _response_event(status_code=200, headers=None, body=None):
    return {
        "interceptorInputVersion": "1.0",
        "mcp": {
            "gatewayRequest": {"headers": {}},
            "gatewayResponse": {"statusCode": status_code, "headers": headers or {}, "body": body},
        },
    }


class TestDecoratorWithRawEvent:
    def test_request_pass_through(self):
        @interceptor()
        def handler(event, context):
            return InterceptorResponse.pass_through(event)

        result = handler(_request_event(headers={"x": "1"}, body={"k": "v"}))

        assert result["interceptorOutputVersion"] == "1.0"
        assert result["mcp"]["transformedGatewayRequest"] == {"headers": {"x": "1"}, "body": {"k": "v"}}

    def test_request_deny(self):
        @interceptor()
        def handler(event, context):
            return InterceptorResponse.deny(403, {"error": "forbidden"})

        result = handler(_request_event())

        assert result["interceptorOutputVersion"] == "1.0"
        resp = result["mcp"]["transformedGatewayResponse"]
        assert resp["statusCode"] == 403
        assert resp["body"] == {"error": "forbidden"}

    def test_response_transform(self):
        @interceptor()
        def handler(event, context):
            return InterceptorResponse.transform_response(200, {}, {"filtered": True})

        result = handler(_response_event(body={"raw": True}))

        assert result["mcp"]["transformedGatewayResponse"]["body"] == {"filtered": True}

    def test_event_point_is_derived(self):
        seen = []

        @interceptor()
        def handler(event, context):
            seen.append(event.point)
            return InterceptorResponse.pass_through(event)

        handler(_request_event())
        handler(_response_event())

        assert seen == ["REQUEST", "RESPONSE"]

    def test_mismatched_input_version_warns_but_parses(self, caplog):
        @interceptor()
        def handler(event, context):
            return InterceptorResponse.pass_through(event)

        event = _request_event()
        event["interceptorInputVersion"] = "9.9"
        result = handler(event)

        assert result["interceptorOutputVersion"] == "1.0"
        assert any("does not match expected" in r.message for r in caplog.records)


class TestExceptionIsCaught:
    """Inverse of the evaluator: a thrown handler must NOT propagate."""

    def test_exception_returns_500(self):
        @interceptor()
        def handler(event, context):
            raise RuntimeError("boom")

        result = handler(_request_event())

        assert result["interceptorOutputVersion"] == "1.0"
        assert result["mcp"]["transformedGatewayResponse"]["statusCode"] == 500

    def test_request_exception_fails_closed(self):
        """A broken REQUEST interceptor blocks the call (500), not pass-through."""

        @interceptor()
        def handler(event, context):
            raise KeyError("missing")

        result = handler(_request_event())

        # Fail closed: a transformedGatewayResponse (deny), never a
        # transformedGatewayRequest that would let the call continue.
        assert "transformedGatewayResponse" in result["mcp"]
        assert "transformedGatewayRequest" not in result["mcp"]

    def test_wrong_return_type_returns_500(self):
        @interceptor()
        def handler(event, context):
            return {"interceptorOutputVersion": "1.0", "mcp": {}}

        result = handler(_request_event())

        assert result["mcp"]["transformedGatewayResponse"]["statusCode"] == 500

    def test_none_return_returns_500(self):
        @interceptor()
        def handler(event, context):
            return None

        result = handler(_request_event())

        assert result["mcp"]["transformedGatewayResponse"]["statusCode"] == 500


class TestFunctoolsWraps:
    def test_preserves_name(self):
        @interceptor()
        def my_interceptor(event, context):
            return InterceptorResponse.pass_through(event)

        assert my_interceptor.__name__ == "my_interceptor"


class TestContextPassthrough:
    def test_context_passed_to_function(self):
        received = []

        @interceptor()
        def handler(event, context):
            received.append(context)
            return InterceptorResponse.pass_through(event)

        ctx = {"function_name": "my-lambda"}
        handler(_request_event(), ctx)

        assert received == [ctx]

    def test_context_defaults_to_none(self):
        received = []

        @interceptor()
        def handler(event, context):
            received.append(context)
            return InterceptorResponse.pass_through(event)

        handler(_request_event())

        assert received == [None]


class TestUnwrapped:
    def test_unwrapped_returns_interceptor_response(self):
        @interceptor()
        def handler(event, context):
            return InterceptorResponse.pass_through(event)

        event = InterceptorEvent.sample_request(headers={"a": "b"})
        result = handler.unwrapped(event, None)

        assert isinstance(result, InterceptorResponse)

    def test_unwrapped_is_original_function(self):
        def my_interceptor(event, context):
            return InterceptorResponse.pass_through(event)

        handler = interceptor()(my_interceptor)
        assert handler.unwrapped is my_interceptor
