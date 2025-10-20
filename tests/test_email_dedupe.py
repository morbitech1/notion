from .fixtures import *  # noqa


class DummyAsyncIMAP:
    def __init__(self, uids):
        self._uids = uids

    async def uid(self, cmd, *args):
        cmd = cmd.lower()
        if cmd == 'fetch':
            # Enumeration form: <range> (UID)
            if len(args) >= 2 and args[1] == '(UID)':
                lines = []
                for i, u in enumerate(self._uids, start=1):
                    lines.append(f"* {i} FETCH (UID {u})".encode())
                return type('R', (), {'result': 'OK', 'lines': lines})()
            # Single message fetch returns RFC822 bytes only (body after header line)
            if not args:
                return type('R', (), {'result': 'NO', 'lines': []})()
            uid = args[0]
            if isinstance(uid, bytes):
                try:
                    uid = int(uid.decode())
                except Exception:
                    uid = int(self._uids[0])
            else:
                uid = int(uid)
            msg = f"Subject: Test {uid}\n\nBody".encode()
            return type('R', (), {'result': 'OK', 'lines': [msg]})()
        raise AssertionError('Unexpected command')


async def test_process_loop_dedupes(monkeypatch):
    imap = DummyAsyncIMAP([10, 11])
    handled = []

    async def handler(msg, uid):  # pragma: no cover - simple
        handled.append(uid)
        return True
    monkeypatch.setattr(we, 'handler_async', handler)
    processed: set[int] = set()
    await we.process_loop_async(imap, poll_interval=0, once=True, start_uid=None, processed_uids=processed)
    # Second pass should not re-handle
    await we.process_loop_async(imap, poll_interval=0, once=True, start_uid=None, processed_uids=processed)
    assert handled == [10, 11]
