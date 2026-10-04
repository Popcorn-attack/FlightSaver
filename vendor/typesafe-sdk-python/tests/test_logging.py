import json
import logging
import traceback

import httpx2
import pytest

from tests.conftest import ClientFactory
from tests.helpers import models
from typesafe_sdk import RetryPolicy, TypeSafeAPIError, TypeSafeError
from typesafe_sdk._core.logging import logger, redact_exception, setup_logging


@pytest.mark.parametrize("status", [200, 400, 429])
@pytest.mark.parametrize(
    "header",
    [
        "Authorization",
        "Proxy-Authorization",
        "X-API-Key",
        "API-Key",
        "Cookie",
        "Set-Cookie",
        "X-Access-Token",
        "X-Client-Secret",
        "x-MiXeD-ToKeN",
    ],
)
async def test_secret_headers_redacted(
    clients: ClientFactory,
    caplog: pytest.LogCaptureFixture,
    status: int,
    header: str,
) -> None:
    attempts = 0

    def handler(request: httpx2.Request) -> httpx2.Response:
        nonlocal attempts
        attempts += 1
        return httpx2.Response(
            status,
            json={"models": []} if status == 200 else {"message": "failure"},
            headers={header: "response-credential", "x-visible": "response-visible"},
        )

    policy = RetryPolicy(backoff_initial=0.001, backoff_max=0.001)
    with caplog.at_level(logging.DEBUG, logger="typesafe_sdk"):
        client = clients(
            handler,
            api_key="auth-credential",
            headers={header: "request-credential", "x-visible": "request-visible"},
            retry=policy,
        )
        if status == 200:
            await models(client)
        else:
            with pytest.raises(TypeSafeAPIError):
                await models(client)
    assert attempts == (3 if status == 429 else 1)
    assert "request-visible" in caplog.text
    assert "response-visible" in caplog.text
    assert "***" in caplog.text
    for secret in ("auth-credential", "request-credential", "response-credential"):
        assert secret not in caplog.text
    if status == 429:
        assert "retry 1" in caplog.text
        assert "retry 2" in caplog.text


@pytest.mark.parametrize(
    "transport_error,attempts",
    [
        (httpx2.LocalProtocolError, 3),
        (httpx2.ConnectError, 3),
        (httpx2.ReadError, 3),
        (httpx2.RemoteProtocolError, 3),
        (httpx2.ReadTimeout, 3),
    ],
)
@pytest.mark.parametrize("credential", ["ts_live_private", "ts_live_quo'te\"slash\\tail"])
@pytest.mark.parametrize("chain", ["cause", "context"])
async def test_transport_errors_do_not_expose_credentials(
    clients: ClientFactory,
    caplog: pytest.LogCaptureFixture,
    transport_error: type[httpx2.RequestError],
    attempts: int,
    credential: str,
    chain: str,
) -> None:
    original_errors: list[httpx2.RequestError] = []
    calls = 0

    def handler(request: httpx2.Request) -> httpx2.Response:
        nonlocal calls
        calls += 1
        # HTTP transports can echo header values in both their messages and exception chains.
        cause = ValueError(f"Rejected authorization: {credential}; provider: {request.headers['x-client-secret']}")
        failure = transport_error(f"Illegal header value {request.headers['authorization'].encode()!r}", request=request)
        if chain == "cause":
            failure.__cause__ = cause
        else:
            failure.__context__ = cause
        original_errors.append(failure)
        raise failure

    policy = RetryPolicy(backoff_initial=0, backoff_max=0)
    with caplog.at_level(logging.DEBUG, logger="typesafe_sdk"):
        client = clients(handler, api_key=credential, headers={"x-client-secret": "provider-credential"}, retry=policy)
        with pytest.raises(TypeSafeError) as caught:
            await models(client)
        error = caught.value
        logger.error("Request failed", exc_info=(type(error), error, error.__traceback__))
    assert credential not in str(error)
    assert credential not in repr(error)
    formatted = "".join(traceback.format_exception(type(error), error, error.__traceback__))
    for value in (credential, repr(credential)[1:-1], "provider-credential"):
        assert value not in formatted
        assert value not in caplog.text
    assert "Illegal header value" in formatted
    assert "Rejected authorization: ***; provider: ***" in formatted
    assert error.__context__ is None
    safe_error = error.__cause__
    assert isinstance(safe_error, transport_error)
    assert safe_error is not original_errors[-1]
    assert safe_error.__traceback__ is None
    with pytest.raises(RuntimeError, match="request property has not been set"):
        _ = safe_error.request
    safe_cause = safe_error.__cause__ if chain == "cause" else safe_error.__context__
    assert isinstance(safe_cause, ValueError)
    assert str(safe_cause) == "Rejected authorization: ***; provider: ***"
    assert original_errors[-1].request.headers["authorization"] == f"Bearer {credential}"
    assert calls == attempts


@pytest.mark.parametrize("header", ["Authorization", "Proxy-Authorization", "X-API-Key", "X-MiXeD-ToKeN"])
def test_exception_redaction_escaped_values(header: str) -> None:
    credential = "private'quoted\"value\\tail"
    value = f"Bearer {credential}" if "authorization" in header.lower() else credential
    error = ValueError(f"raw={credential}; bytes={credential.encode()!r}; json={json.dumps(credential)}")
    safe_error = redact_exception(error, {header: value})
    assert isinstance(safe_error, ValueError)
    assert str(safe_error) == "raw=***; bytes=b'***'; json=\"***\""
    assert str(error).startswith(f"raw={credential};")


def test_exception_redaction_shared_causes_cycles_and_notes() -> None:
    error = httpx2.ConnectError("Failed using private-key")
    cause = ValueError("Rejected private-key")
    error.__cause__ = cause
    error.__context__ = cause
    cause.__context__ = error
    # Exception notes are displayed by Python 3.11+, but can be attached on 3.10 too.
    cause.__dict__["__notes__"] = ["Credential: private-key"]
    safe_error = redact_exception(error, {"Authorization": "Bearer private-key"})
    assert isinstance(safe_error, httpx2.ConnectError)
    assert isinstance(safe_error.__cause__, ValueError)
    assert safe_error.__cause__ is safe_error.__context__
    assert safe_error.__cause__.__context__ is safe_error
    assert safe_error.__cause__.__dict__["__notes__"] == ["Credential: ***"]
    formatted = "".join(traceback.format_exception(type(safe_error), safe_error, None))
    assert "private-key" not in formatted
    assert "Rejected ***" in formatted
    assert str(cause) == "Rejected private-key"


def test_exception_redaction_structured_constructor() -> None:
    error = UnicodeDecodeError("ascii", b"private-key", 0, 1, "private-key is invalid")
    safe_error = redact_exception(error, {"Authorization": "Bearer private-key"})
    assert "UnicodeDecodeError" in str(safe_error)
    assert "*** is invalid" in str(safe_error)
    assert "private-key" not in repr(safe_error)
    assert safe_error.__cause__ is None
    assert safe_error.__context__ is None


def test_exception_redaction_preserves_network_diagnostics() -> None:
    cause = OSError(101, "Network is unreachable")
    error = httpx2.ConnectError(str(cause))
    error.__cause__ = cause
    safe_error = redact_exception(error, {"Authorization": "Bearer private-key"})
    assert type(safe_error) is type(error)
    assert str(safe_error) == str(error)
    assert isinstance(safe_error.__cause__, OSError)
    assert str(safe_error.__cause__) == str(cause)


@pytest.mark.parametrize(
    "level,expected",
    [
        (logging.DEBUG, {logging.DEBUG, logging.INFO}),
        (logging.INFO, {logging.INFO}),
        (logging.WARNING, set()),
    ],
)
async def test_logger_level_controls_output(
    clients: ClientFactory,
    caplog: pytest.LogCaptureFixture,
    level: int,
    expected: set[int],
) -> None:
    with caplog.at_level(level, logger="typesafe_sdk"):
        await models(clients(lambda request: httpx2.Response(200, json={"models": []})))
    records = [record for record in caplog.records if record.name == "typesafe_sdk"]
    assert {record.levelno for record in records} == expected
    if logging.INFO in expected:
        summaries = [record.getMessage() for record in records if record.levelno == logging.INFO]
        assert len(summaries) == 1
        assert "GET" in summaries[0]
    if logging.DEBUG not in expected:
        assert all("headers=" not in record.getMessage() for record in records)


@pytest.mark.parametrize(
    "value,expected",
    [
        ("debug", logging.DEBUG),
        ("info", logging.INFO),
        ("off", logging.CRITICAL + 1),
        ("bogus", logging.NOTSET),
        ("", logging.NOTSET),
    ],
)
def test_setup_logging_from_env(monkeypatch: pytest.MonkeyPatch, value: str, expected: int) -> None:
    original = logger.level
    try:
        logger.setLevel(logging.NOTSET)
        monkeypatch.setenv("TYPESAFE_LOG_LEVEL", value)
        setup_logging()
        assert logger.level == expected
    finally:
        logger.setLevel(original)
