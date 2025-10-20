from .fixtures import *  # noqa


def test_decoded_simple_utf8():
    # UTF-8 encoded word form (RFC 2047)
    # Encoded form of '日本語 テスト'
    raw = 'Subject: =?UTF-8?B?5pel5pys6KqeIOODhuOCueODiA==?=\n\nBody'
    msg = email.message_from_bytes(raw.encode())
    subject = eu.get_decoded_subject(msg)
    assert subject == '日本語 テスト'


def test_decoded_quoted_printable_and_ticket_id():
    # Quoted printable segments and embedded ticket id pattern
    # Encoded subject for "Support case [1234567890] – Café" (en dash + accent)
    subject_text = 'Support case [1234567890] – Café'
    # Build message via EmailMessage to ensure proper header folding
    m = EmailMessage()
    m['Subject'] = subject_text
    m.set_content('Body')
    msg = email.message_from_bytes(m.as_bytes())
    # Simulate encoded header by re-parsing; Python may not encode if ascii-safe
    subj = eu.get_decoded_subject(msg)
    assert 'Support case' in subj
    assert '1234567890' in subj
    assert 'Café' in subj
    # Ensure ticket id extraction works on decoded subject
    ticket = nu.extract_ticket_id(msg)
    assert ticket == '1234567890'
