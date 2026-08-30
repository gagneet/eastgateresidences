import html as html_lib

import nh3
from pydantic import ValidationError

from models.chat import ChatGroupCreate, GroupMessageCreate
from models.communication import AnnouncementCreate, NoticeCreate, MessageCreate
from models.maintenance import MaintenanceRequestCreate


def test_communication_max_length_constraints():
    # Test AnnouncementCreate content limit
    try:
        AnnouncementCreate(title="Test", content="a" * 5001)
        assert False, "Should have raised ValidationError"
    except ValidationError:
        pass

    # Test NoticeCreate content limit
    try:
        NoticeCreate(title="Test", content="a" * 5001)
        assert False, "Should have raised ValidationError"
    except ValidationError:
        pass

    # Test MessageCreate content limit
    try:
        MessageCreate(content="a" * 1001)
        assert False, "Should have raised ValidationError"
    except ValidationError:
        pass


def test_chat_max_length_constraints():
    # Test ChatGroupCreate name limit
    try:
        ChatGroupCreate(name="a" * 101)
        assert False, "Should have raised ValidationError"
    except ValidationError:
        pass

    # Test GroupMessageCreate content limit
    try:
        GroupMessageCreate(content="a" * 1001)
        assert False, "Should have raised ValidationError"
    except ValidationError:
        pass


def test_maintenance_max_length_constraints():
    # Test MaintenanceRequestCreate description limit
    try:
        MaintenanceRequestCreate(
            title="Test",
            description="a" * 2001,
            category="plumbing",
            location="Unit 1"
        )
        assert False, "Should have raised ValidationError"
    except ValidationError:
        pass


def test_sanitization_logic():
    # Test html.escape for plain text
    dangerous_title = "<script>alert('XSS')</script>"
    safe_title = html_lib.escape(dangerous_title)
    assert "<script>" not in safe_title
    assert "&lt;script&gt;" in safe_title

    # Test nh3.clean for rich text
    dangerous_content = "<p>Hello</p><script>alert('XSS')</script><img src=x onerror=alert(1)>"
    safe_content = nh3.clean(dangerous_content)
    assert "<script>" not in safe_content
    assert "onerror" not in safe_content
    assert "<p>Hello</p>" in safe_content


if __name__ == "__main__":
    test_communication_max_length_constraints()
    test_chat_max_length_constraints()
    test_maintenance_max_length_constraints()
    test_sanitization_logic()
    print("All security enhancement tests passed!")
