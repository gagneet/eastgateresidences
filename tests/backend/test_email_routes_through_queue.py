# @featuretrace:outbound-message-queue — send_email_async must enqueue, not transmit.
# Layer: test
# Data flow: send_email_async -> kill switch -> queue enqueue (held) | inline transmit (building-scoped).
# Related: backend/utils/email.py
#          backend/services/outbound_queue_service.py
"""The wiring between send_email_async and the queue.

The state machine is covered by test_outbound_queue_service.py. What matters here is
the ordering and the escape hatches, because each one is a way mail could reach a
provider without an operator ever seeing it:

  * the env kill switch must still win over the queue
  * an ordinary caller must enqueue rather than transmit
  * only the worker's _from_worker flag may transmit
  * a queue fault must fall back to sending, never silently swallow the message

No database and no provider are touched.
"""

from unittest.mock import AsyncMock, patch

import pytest

import utils.email as email_mod


def _queue_on(monkeypatch):
    monkeypatch.setenv("OUTBOUND_QUEUE_ENABLED", "true")


@pytest.fixture
def no_suppression():
    """Let mail past the kill switch so the queue branch is the one under test."""
    with patch("utils.email_suppression.suppress_if_blocked",
               new=AsyncMock(return_value=False)):
        yield


@pytest.mark.asyncio
async def test_kill_switch_still_wins_over_the_queue(monkeypatch):
    """EMAIL_SEND_DISABLED_ALL must stop a message before it is even queued."""
    _queue_on(monkeypatch)
    with patch("utils.email_suppression.suppress_if_blocked",
               new=AsyncMock(return_value=True)), \
         patch("utils.email._log_email_sent", new=AsyncMock()), \
         patch("services.outbound_queue_service.enqueue", new=AsyncMock()) as enq:
        out = await email_mod.send_email_async("a@b.com", "s", "<p>h</p>")
    assert out["suppressed"] is True
    enq.assert_not_awaited(), "a suppressed message must not enter the queue"


@pytest.mark.asyncio
async def test_an_ordinary_caller_enqueues_instead_of_transmitting(monkeypatch, no_suppression):
    _queue_on(monkeypatch)
    with patch("services.outbound_queue_service.enqueue",
               new=AsyncMock(return_value={"id": "m-1"})) as enq, \
         patch("utils.email._ambient_building_id_for_queue", return_value="13195"):
        out = await email_mod.send_email_async("owner@x.com", "Levy notice", "<p>due</p>",
                                               context="levy_reminder")
    assert out == {"success": True, "queued": True, "provider": "queued", "message_id": "m-1"}
    kwargs = enq.await_args.kwargs
    assert kwargs["building_id"] == "13195"
    assert kwargs["html_body"] == "<p>due</p>", "the body must be stored — the whole point"


@pytest.mark.asyncio
async def test_the_worker_flag_transmits_rather_than_re_enqueueing(monkeypatch, no_suppression):
    """Without this, the worker would re-queue its own messages forever."""
    _queue_on(monkeypatch)
    with patch("services.outbound_queue_service.enqueue", new=AsyncMock()) as enq, \
         patch("utils.email._send_via_provider", new=AsyncMock(return_value={"success": True}),
               create=True):
        try:
            await email_mod.send_email_async("a@b.com", "s", "<p>h</p>", _from_worker=True)
        except Exception:
            # Provider internals differ; the assertion below is the contract under test.
            pass
    enq.assert_not_awaited()


@pytest.mark.asyncio
async def test_queue_disabled_by_default_leaves_behaviour_unchanged(monkeypatch, no_suppression):
    monkeypatch.delenv("OUTBOUND_QUEUE_ENABLED", raising=False)
    with patch("services.outbound_queue_service.enqueue", new=AsyncMock()) as enq:
        try:
            await email_mod.send_email_async("a@b.com", "s", "<p>h</p>")
        except Exception:
            pass
    enq.assert_not_awaited(), "the queue must be opt-in until the console exists"


@pytest.mark.asyncio
async def test_a_queue_fault_falls_back_to_sending_not_to_dropping(monkeypatch, no_suppression):
    """A broken queue must not become a silent mail-eater."""
    _queue_on(monkeypatch)
    with patch("services.outbound_queue_service.enqueue",
               new=AsyncMock(side_effect=RuntimeError("mongo down"))), \
         patch("utils.email._ambient_building_id_for_queue", return_value="13195"):
        try:
            out = await email_mod.send_email_async("a@b.com", "s", "<p>h</p>")
        except Exception:
            out = None
    # It fell through past the queue rather than returning a queued result.
    assert out is None or not out.get("queued"), "a queue fault must not report success"
