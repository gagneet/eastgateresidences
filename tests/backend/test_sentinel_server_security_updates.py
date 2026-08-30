import inspect

import pytest
from fastapi import Request

from server import check_registration_update_token, submit_registration_update


@pytest.mark.asyncio
async def test_registration_update_rate_limiting_decorators():
    # Verify that the decorators are present by checking the function source
    # because FastAPI/slowapi might wrap them in a way that hides attributes in some environments
    source_check = inspect.getsource(check_registration_update_token)
    assert "@rate_limit" in source_check

    source_submit = inspect.getsource(submit_registration_update)
    assert "@rate_limit" in source_submit


@pytest.mark.asyncio
async def test_registration_update_endpoints_have_request_param():
    # Verify that the new Request parameter is present
    sig = inspect.signature(check_registration_update_token)
    assert "request" in sig.parameters

    sig = inspect.signature(submit_registration_update)
    assert "request" in sig.parameters
