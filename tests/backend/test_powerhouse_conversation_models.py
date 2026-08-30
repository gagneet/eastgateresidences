from __future__ import annotations

import pytest
from pydantic import ValidationError

from models.powerhouse_conversation import (
    ConversationThreadCreate,
    ConvertMessageToWorkflowRequest,
    InboundEmailEventCreate,
    WorkflowInstanceCreate,
)


def test_conversation_thread_model_rejects_unknown_fields():
    with pytest.raises(ValidationError):
        ConversationThreadCreate(
            subject="Subject",
            source_channel="portal_message",
            body="Body",
            building_id="forbidden-extra",
        )


def test_inbound_email_event_requires_message_id():
    with pytest.raises(ValidationError):
        InboundEmailEventCreate(
            message_id="",
            subject="Subject",
            from_email="owner@example.com",
            to=["inbox@example.com"],
        )


def test_workflow_instance_minimum_payload_validates():
    payload = WorkflowInstanceCreate(template_key="maintenance-from-conversation", title="Investigate leak")
    assert payload.template_key == "maintenance-from-conversation"
    assert payload.initial_payload == {}


def test_convert_message_to_workflow_rejects_unknown_fields():
    with pytest.raises(ValidationError):
        ConvertMessageToWorkflowRequest(
            workflow_template_key="maintenance-from-conversation",
            reason="x",
            thread_id="not-allowed",
        )
