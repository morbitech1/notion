from .fixtures import *  # noqa


class GmailLikeAsyncIMAP:
    def __init__(self, uid_to_raw):
        self.uid_to_raw = uid_to_raw

    async def uid(self, cmd, *args):  # pragma: no cover - simple stub
        cmd = cmd.lower()
        if cmd == 'fetch':
            # Enumeration form first: range with (UID)
            if len(args) >= 2 and args[1] == '(UID)':
                # Provide one UID (10)
                return type('R', (), {'result': 'OK', 'lines': [b'* 1 FETCH (UID 10)']})()
            if not args:
                return type('R', (), {'result': 'NO', 'lines': []})()
            uid = int(args[0])
            raw = self.uid_to_raw.get(uid)
            if raw is None:
                return type('R', (), {'result': 'OK', 'lines': []})()
            header = b'FLAGS () X-GM-THRID 1998438563533830 X-GM-MSGID 1844298287584327728 RFC822'
            return type('R', (), {'result': 'OK', 'lines': [header, raw]})()
        return type('R', (), {'result': 'OK', 'lines': []})()


async def test_fetch_message_with_attrs_injects_headers(monkeypatch):
    raw_email = b"Subject: Test Extended\n\nBody"
    imap = GmailLikeAsyncIMAP({10: raw_email})
    captured: list[Message] = []

    async def handler(msg: Message, uid: int):
        captured.append(msg)
        return True
    monkeypatch.setattr(we, 'handler_async', handler)
    await we.process_loop_async(imap, poll_interval=0, once=True, start_uid=None)
    assert captured, 'Handler not invoked'
    msg = captured[0]
    assert msg.get('X-GM-THRID') == '1998438563533830'
    assert msg.get('X-GM-MSGID') == '1844298287584327728'
