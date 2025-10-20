from email.message import EmailMessage
from typing import Any

from .fixtures import *  # noqa


class ArchiveIMAP:
    def __init__(self):
        self.uids = [7]
        self.copied = []
        self.stored = []
        self.expunged = False

    async def uid(self, command, *args) -> Any:  # async style
        c = command.lower()
        if c == 'fetch':
            # Enumeration form: range + (UID)
            if len(args) >= 2 and args[1] == '(UID)':
                lines = []
                for i, uid in enumerate(self.uids, start=1):
                    lines.append(f"* {i} FETCH (UID {uid})".encode())
                return type('R', (), {'result': 'OK', 'lines': lines})()
            # Single message fetch: return body bytes with extended header
            if not args:
                return type('R', (), {'result': 'NO', 'lines': []})()
            uid = int(args[0])
            msg = EmailMessage()
            msg['Subject'] = 'Archive test'
            msg['To'] = 'support@company.domain'
            msg['From'] = 'engineering@company.domain'
            msg.set_content('Body')
            header = b'FLAGS () X-GM-THRID 1 X-GM-MSGID 2 RFC822'
            return type('R', (), {'result': 'OK', 'lines': [header, msg.as_bytes()]})()
        if c == 'copy':
            self.copied.append(args)
            return type('R', (), {'result': 'OK', 'lines': []})()
        if c == 'store':
            self.stored.append(args)
            return type('R', (), {'result': 'OK', 'lines': []})()
        return type('R', (), {'result': 'OK', 'lines': []})()

    async def expunge(self):
        self.expunged = True
        return type('R', (), {'result': 'OK', 'lines': []})()

    async def select(self, folder, readonly=True):
        return type('R', (), {'result': 'OK', 'lines': []})()


async def test_auto_archive(monkeypatch):
    imap = ArchiveIMAP()

    async def _connect(*a, **k):
        return imap
    monkeypatch.setattr(ia, 'connect_imap_async', _connect)
    # prevent network in handler path
    monkeypatch.setattr(nu.config, 'NOTION_SUPPORT_CASES_DB_ID', '')  # disable case creation
    monkeypatch.setattr(we, 'AUTO_ARCHIVE_PROCESSED', True)

    async def dummy_handler(m, uid):
        return True
    monkeypatch.setattr(we, 'handler_async', dummy_handler)
    await we.run_watcher_async(poll_interval=0, once=True, start_uid=None)
    assert imap.copied  # message copied to archive folder (alias present)
    assert any(flag for flag in imap.stored if b'\\Deleted' or '\\Deleted' in flag[-1])
    assert imap.expunged


class NoArchiveIMAP(ArchiveIMAP):
    async def uid(self, command, *args) -> Any:
        c = command.lower()
        if c == 'fetch':
            if len(args) >= 2 and args[1] == '(UID)':
                # enumeration returns UID 8
                return type('R', (), {'result': 'OK', 'lines': [b'* 1 FETCH (UID 8)']})()
            if not args:
                return type('R', (), {'result': 'NO', 'lines': []})()
            uid = int(args[0])
            msg = EmailMessage()
            msg['Subject'] = 'No archive test'
            msg['To'] = 'someone@example.com'
            msg['From'] = 'external@example.com'
            msg.set_content('Body')
            header = b'FLAGS () RFC822'
            return type('R', (), {'result': 'OK', 'lines': [header, msg.as_bytes()]})()
        return await super().uid(command, *args)


async def test_skip_archive_for_irrelevant(monkeypatch):
    imap = NoArchiveIMAP()

    async def _connect(*a, **k):
        return imap
    async def handler(msg, uid):
        return False
    monkeypatch.setattr(we, 'handler_async', handler)
    monkeypatch.setattr(ia, 'connect_imap_async', _connect)
    monkeypatch.setattr(nu.config, 'NOTION_SUPPORT_CASES_DB_ID', '')
    await we.run_watcher_async(poll_interval=0, once=True, start_uid=None)
    assert not imap.copied
    assert not imap.expunged
