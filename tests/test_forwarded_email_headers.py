from .fixtures import *  # noqa


def build_forwarded_email():
    msg = EmailMessage()
    msg['From'] = 'alice@example.com'
    msg['To'] = 'support@company.domain'
    msg['Subject'] = 'Fwd: Issue with login'
    body = """Hi team,\n\nPlease see the issue below.\n\n---------- Forwarded message ---------\nFrom: Bob User <bob@customer.com>\nTo: Support Team <support@company.domain>\nCc: Carol <carol@customer.com>\nSubject: Issue with login\n\nActual forwarded body starts here.\n"""
    msg.set_content(body)
    return msg


def test_forwarded_header_extraction_prefers_original():
    msg = build_forwarded_email()
    from_addrs, to_addrs, cc_addrs = eu.get_message_addresses(msg)
    assert from_addrs == ['bob@customer.com']  # original sender
    # The forwarded To should still include support alias
    assert 'support@company.domain' in to_addrs
    assert set(cc_addrs) == {'carol@customer.com', 'alice@example.com'}


def build_non_forward_email():
    msg = EmailMessage()
    msg['From'] = 'dave@customer.com'
    msg['To'] = 'support@company.domain'
    msg['Subject'] = 'Direct: Help needed'
    msg.set_content('Regular email body without forwarding headers.')
    return msg


def test_non_forward_email_unchanged():
    msg = build_non_forward_email()
    from_addrs, to_addrs, cc_addrs = eu.get_message_addresses(msg)
    assert from_addrs == ['dave@customer.com']
    assert to_addrs == ['support@company.domain']
    assert cc_addrs == []


def build_forwarded_email_folded_cc():
    msg = EmailMessage()
    msg['From'] = 'alice@example.com'
    msg['To'] = 'support@company.domain'
    msg['Subject'] = 'Fwd: Multi-line CC'
    # Simulate RFC 5322 header folding where CC line wraps with indentation
    body = (
        "Intro text.\n\n---------- Forwarded message ---------\n"
        "From: Bob User <bob@customer.com>\n"
        "To: Support Team <support@company.domain>\n"
        "Cc: Carol <carol@customer.com>,\n"
        "    Dan <dan@customer.com>, Eve <eve@customer.com>\n"
        "Subject: Multi-line CC example\n\n"
        "Body start here.\n"
    )
    msg.set_content(body)
    return msg


def test_forwarded_header_folded_cc():
    msg = build_forwarded_email_folded_cc()
    from_addrs, to_addrs, cc_addrs = eu.get_message_addresses(msg)
    assert from_addrs == ['bob@customer.com']
    # Support alias preserved
    assert 'support@company.domain' in to_addrs
    # Order not strictly guaranteed (set compare) but all four must appear
    assert set(cc_addrs) == {'carol@customer.com', 'dan@customer.com', 'eve@customer.com', 'alice@example.com'}
