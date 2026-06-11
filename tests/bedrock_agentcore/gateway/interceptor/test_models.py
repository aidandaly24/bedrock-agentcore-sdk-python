"""Tests for interceptor models, parsing, and fluent constructors."""

import pytest
from pydantic import ValidationError

from bedrock_agentcore.gateway.interceptor import (
    GatewayRequest,
    InterceptorEvent,
    InterceptorResponse,
)


class TestFromLambdaEvent:
    def test_request_only(self):
        event = InterceptorEvent.from_lambda_event(
            {"mcp": {"gatewayRequest": {"path": "/p", "httpMethod": "GET", "headers": {"a": "b"}, "body": {"k": 1}}}}
        )
        assert event.point == "REQUEST"
        assert event.request is not None
        assert event.request.http_method == "GET"
        assert event.request.headers == {"a": "b"}
        assert event.response is None

    def test_response_carries_both_request_and_response(self):
        event = InterceptorEvent.from_lambda_event(
            {
                "mcp": {
                    "gatewayRequest": {"headers": {"auth": "x"}},
                    "gatewayResponse": {"statusCode": 201, "headers": {}, "body": {"ok": True}},
                }
            }
        )
        assert event.point == "RESPONSE"
        assert event.response is not None
        assert event.response.status_code == 201
        # RESPONSE events carry the original request too.
        assert event.request is not None
        assert event.request.headers == {"auth": "x"}

    def test_invocation_index(self):
        event = InterceptorEvent.from_lambda_event(
            {"mcp": {"gatewayResponse": {"statusCode": 200}, "invocationIndex": 3}}
        )
        assert event.invocation_index == 3

    def test_raw_is_preserved(self):
        raw = {"mcp": {"gatewayRequest": {"headers": {}}}, "extra": "kept"}
        event = InterceptorEvent.from_lambda_event(raw)
        assert event.raw["extra"] == "kept"


class TestSampleBuilders:
    def test_sample_request(self):
        event = InterceptorEvent.sample_request(headers={"authorization": "Bearer x"}, body={"q": 1})
        assert event.point == "REQUEST"
        assert event.request.headers == {"authorization": "Bearer x"}
        # The raw form round-trips through from_lambda_event.
        reparsed = InterceptorEvent.from_lambda_event(event.raw)
        assert reparsed.point == "REQUEST"
        assert reparsed.request.headers == {"authorization": "Bearer x"}

    def test_sample_response(self):
        event = InterceptorEvent.sample_response(status_code=403, body={"e": 1}, request_headers={"a": "b"})
        assert event.point == "RESPONSE"
        assert event.response.status_code == 403
        assert event.request.headers == {"a": "b"}
        reparsed = InterceptorEvent.from_lambda_event(event.raw)
        assert reparsed.point == "RESPONSE"
        assert reparsed.response.status_code == 403


class TestInterceptorResponseConstructors:
    def test_pass_through_request(self):
        event = InterceptorEvent.sample_request(headers={"h": "1"}, body={"b": 2})
        env = InterceptorResponse.pass_through(event).to_envelope()
        assert env["mcp"]["transformedGatewayRequest"] == {"headers": {"h": "1"}, "body": {"b": 2}}

    def test_pass_through_response(self):
        event = InterceptorEvent.sample_response(status_code=200, headers={"c": "d"}, body={"r": 1})
        env = InterceptorResponse.pass_through(event).to_envelope()
        assert env["mcp"]["transformedGatewayResponse"]["statusCode"] == 200
        assert env["mcp"]["transformedGatewayResponse"]["body"] == {"r": 1}

    def test_transform_request(self):
        env = InterceptorResponse.transform_request({"h": "1"}, {"b": 2}).to_envelope()
        assert env["mcp"]["transformedGatewayRequest"] == {"headers": {"h": "1"}, "body": {"b": 2}}

    def test_transform_response(self):
        env = InterceptorResponse.transform_response(204, {"h": "1"}, None).to_envelope()
        assert env["mcp"]["transformedGatewayResponse"]["statusCode"] == 204

    def test_deny_defaults_content_type(self):
        env = InterceptorResponse.deny(403, {"error": "no"}).to_envelope()
        resp = env["mcp"]["transformedGatewayResponse"]
        assert resp["statusCode"] == 403
        assert resp["headers"] == {"Content-Type": "application/json"}

    def test_deny_custom_headers(self):
        env = InterceptorResponse.deny(401, {"error": "no"}, headers={"WWW-Authenticate": "Bearer"}).to_envelope()
        assert env["mcp"]["transformedGatewayResponse"]["headers"] == {"WWW-Authenticate": "Bearer"}

    def test_to_envelope_always_stamps_version(self):
        env = InterceptorResponse.transform_request({}, None).to_envelope()
        assert env["interceptorOutputVersion"] == "1.0"


class TestGatewayRequestAliases:
    def test_http_method_alias_parses(self):
        req = GatewayRequest.model_validate({"httpMethod": "POST", "headers": {}})
        assert req.http_method == "POST"


class TestInterceptorResponseInvariant:
    """Exactly one of transformed_request / transformed_response must be set."""

    def test_empty_response_rejected(self):
        with pytest.raises(ValidationError):
            InterceptorResponse()

    def test_both_fields_rejected(self):
        with pytest.raises(ValidationError):
            InterceptorResponse(
                transformed_request={"headers": {}, "body": {}},
                transformed_response={"statusCode": 200, "headers": {}, "body": {}},
            )

    def test_single_field_accepted(self):
        resp = InterceptorResponse(transformed_request={"headers": {}, "body": {}})
        assert resp.to_envelope()["mcp"].keys() == {"transformedGatewayRequest"}
