from .fixtures import *  # noqa


class FakeAsyncResponse:
    def __init__(self, result: str, lines):
        self.result = result
        self.lines = lines


class FakeAsyncIMAP:
    """Minimal async IMAP stub exposing the subset we use."""

    def __init__(self, uids):
        self.uids = uids
        self.fetch_count = 0

    async def wait_hello_from_server(self):  # pragma: no cover - noop
        return

    async def login(self, user, password):  # pragma: no cover - noop
        return

    async def select(self, folder, readonly=True):
        return FakeAsyncResponse('OK', [])

    async def uid(self, command, *args):
        cmd = command.lower()
        if cmd == 'fetch':
            # Enumeration path uses range like '1:*' with (UID)
            if len(args) >= 2 and args[1] == '(UID)':
                # Return synthetic lines each containing a UID token
                lines = []
                for i, uid in enumerate(self.uids, start=1):
                    lines.append(f"* {i} FETCH (UID {uid})".encode())
                return FakeAsyncResponse('OK', lines)
            # Single message fetch path (extended fetch or RFC822)
            if not args:
                raise RuntimeError('Missing UID argument')
            uid = int(args[0])
            msg = EmailMessage()
            msg['Subject'] = f'Subject {uid}'
            msg.set_content('Body')
            if args and 'X-GM-THRID' in args[-1]:  # extended fetch path
                return FakeAsyncResponse('OK', [b'FLAGS () X-GM-THRID 123 X-GM-MSGID 456 RFC822', msg.as_bytes()])
            return FakeAsyncResponse('OK', [msg.as_bytes()])
        raise RuntimeError('Unknown command')


async def test_fetch_and_handler_invocation(monkeypatch):
    fake = FakeAsyncIMAP([5, 6])
    handled = []
    async def handler(msg, uid):  # sync handler acceptable
        handled.append(msg.get('Subject'))
    monkeypatch.setattr(we, 'handler_async', handler)

    # Monkeypatch async connect to return our fake client directly
    async def _connect(*a, **k):  # pragma: no cover - simple stub
        return fake
    monkeypatch.setattr(ia, 'connect_imap_async', _connect)
    # Run watcher once
    await we.run_watcher_async(poll_interval=0, once=True, start_uid=None)
    assert handled == ['Subject 5', 'Subject 6']
