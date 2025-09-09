from email.message import EmailMessage
from email.utils import format_datetime
from datetime import datetime, timezone

import pytest

import b4
import b4.dig


@pytest.fixture
def sample_messages() -> dict[str, list[EmailMessage]]:
    def make_message(msgid: str, subject: str, dt: datetime) -> EmailMessage:
        message = EmailMessage()
        message['Message-Id'] = f'<{msgid}>'
        message['Subject'] = subject
        message['Date'] = format_datetime(dt)
        message.set_content('test body')
        return message

    base_time = datetime(2024, 9, 1, 12, 0, tzinfo=timezone.utc)
    return {
        'orig@example.com': [
            make_message('orig@example.com', '[PATCH] origin', base_time),
            make_message('reply1@example.com', 'Re: [PATCH] origin', base_time.replace(hour=13)),
        ],
        'related@example.com': [
            make_message('related@example.com', '[PATCH v2] origin', base_time.replace(day=2)),
            make_message('reply1@example.com', 'Re: [PATCH] origin', base_time.replace(hour=13)),
        ],
        'another@example.com': [
            make_message('another@example.com', '[PATCH] another feature', base_time.replace(day=3)),
        ],
    }


def test_parse_agent_response_parses_json_fragment() -> None:
    response = "Some header text\n[\n  {\"msgid\": \"<foo@example.com>\", \"relationship\": \"parent\", \"reason\": \"Parent thread\"},\n  {\"msgid\": \"bar@example.com\", \"relationship\": \"related\", \"reason\": \"Same series\"}\n]\nTrailing text"
    parsed = b4.dig.parse_agent_response(response)
    assert parsed == [
        {
            'msgid': 'foo@example.com',
            'relationship': 'parent',
            'reason': 'Parent thread',
        },
        {
            'msgid': 'bar@example.com',
            'relationship': 'related',
            'reason': 'Same series',
        },
    ]


def test_parse_agent_response_falls_back_to_regex() -> None:
    response = "Related messages: foo@example.com and <foo@example.com> plus extra rel@example.com"
    parsed = b4.dig.parse_agent_response(response)
    assert parsed == [
        {
            'msgid': 'foo@example.com',
            'relationship': 'related',
            'reason': 'Found in agent response',
        },
        {
            'msgid': 'rel@example.com',
            'relationship': 'related',
            'reason': 'Found in agent response',
        },
    ]


def test_download_and_combine_threads(tmp_path, monkeypatch, sample_messages) -> None:
    fetch_calls: list[str] = []

    def fake_get_pi_thread_by_msgid(msgid: str, nocache: bool = False):  # type: ignore[override]
        fetch_calls.append(msgid)
        return sample_messages.get(msgid, [])

    monkeypatch.setattr(b4, 'get_pi_thread_by_msgid', fake_get_pi_thread_by_msgid)

    output_path = tmp_path / 'combined.mbox'
    related = [
        {'msgid': '<related@example.com>', 'relationship': 'reply', 'reason': 'Follow-up'},
        {'msgid': 'related@example.com', 'relationship': 'reply', 'reason': 'Duplicate entry'},
        {'msgid': 'another@example.com', 'relationship': 'related', 'reason': 'Another thread'},
    ]

    total = b4.dig.download_and_combine_threads('orig@example.com', related, str(output_path))

    assert total == 4
    assert output_path.exists()

    with output_path.open('rt', encoding='utf-8') as fhandle:
        contents = fhandle.read()
    assert contents.count('From mboxrd@z Thu Jan  1 00:00:00 1970') == total

    assert fetch_calls == ['orig@example.com', 'related@example.com', 'another@example.com']
