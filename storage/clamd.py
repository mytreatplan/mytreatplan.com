"""A minimal clamd client (AD-9, E6): INSTREAM over clamd's unix socket.

    verdict = clamd.scan_stream(chunks, socket_path=settings.STORAGE_CLAMD_SOCKET)

The protocol (clamd(8)): send `zINSTREAM\\0`, then the data as chunks, each a 4-byte
big-endian length followed by that many bytes, then a zero-length chunk; clamd answers
one NUL-terminated line:

    stream: OK                      -> Verdict(OK)
    stream: <signature> FOUND       -> Verdict(FOUND, signature)
    INSTREAM size limit exceeded. ERROR -> Verdict(ERROR, over_limit=True)
    <anything else> ERROR           -> Verdict(ERROR)  (undecided: retried)
    anything else                   -> Verdict(ERROR)  (undecided: retried)

With `AlertExceedsMax yes` and the encrypted/broken-file alerts on (deploy/clamav/
clamd.conf), a file clamd could not fully scan answers FOUND (`Heuristics.Limits.
Exceeded.*`, `Heuristics.Encrypted.*`, `Heuristics.Broken.*`). Only OK passes a file.

`ClamdUnavailable` is raised when clamd cannot be reached or gives no answer (refused,
timed out, closed without a reply): nothing was decided, and the caller retries.

clamd closes the connection as soon as the stream exceeds StreamMaxLength, while the
data is still being sent; its reply is then still readable on the unix socket, so a send
that fails (or a reply that arrives before the end, polled before each chunk) is read.
Such an early reply is never OK: a FOUND or an ERROR stands, anything else is a
scan_error (undecided, so retried).
"""
import re
import select
import socket
import struct
from dataclasses import dataclass

OK = 'ok'
FOUND = 'found'
ERROR = 'error'

COMMAND = b'zINSTREAM\0'
CHUNK_SIZE = 1024 * 1024
# The longest reply read; clamd's are one short line.
MAX_REPLY = 4096
_SIGNATURE_RE = re.compile(r'^[A-Za-z0-9._:/+-]{1,200}$')
_FOUND_RE = re.compile(r'^stream: (.+) FOUND$')
# Signature prefixes clamd uses when it could not fully scan the file (limits, encrypted
# or broken files): rejected like any FOUND, reported with their own outcome code.
LIMIT_PREFIX = 'Heuristics.Limits.Exceeded'
UNSCANNABLE_PREFIXES = ('Heuristics.Encrypted', 'Heuristics.Broken')


class ClamdUnavailable(OSError):
    """clamd could not be reached or gave no answer; nothing was decided."""


@dataclass(frozen=True)
class Verdict:
    result: str          # OK, FOUND or ERROR
    signature: str = ''  # FOUND only: the signature name (safe characters only)
    over_limit: bool = False  # ERROR only: the stream exceeded StreamMaxLength

    @property
    def decided(self):
        """Whether this answer decides the file: OK passes it; a FOUND or a size-limit
        ERROR rejects it. Any other ERROR or unknown answer decides nothing (decision
        2026-11-08): the scan is tried again."""
        return self.result in (OK, FOUND) or self.over_limit

    @property
    def code(self):
        """The outcome code that goes into logs and reports."""
        if self.result == OK:
            return 'clean'
        if self.over_limit:
            return 'limit_exceeded'
        if self.result == FOUND:
            if self.signature.startswith(LIMIT_PREFIX):
                return 'limit_exceeded'
            if self.signature.startswith(UNSCANNABLE_PREFIXES):
                return 'unscannable'
            return 'infected'
        return 'scan_error'


def parse_reply(raw):
    """The Verdict for clamd's raw reply (bytes, NUL or newline terminated)."""
    text = raw.decode('utf-8', 'replace').strip('\0\r\n ')
    if text == 'stream: OK':
        return Verdict(OK)
    match = _FOUND_RE.match(text)
    if match:
        signature = match.group(1).strip()
        return Verdict(FOUND, signature if _SIGNATURE_RE.match(signature) else 'unknown')
    # "... ERROR" (a size limit, a read error) and anything unexpected: never OK.
    return Verdict(ERROR, over_limit=text.endswith('size limit exceeded. ERROR'))


def _read_reply(sock):
    reply = b''
    while len(reply) < MAX_REPLY:
        try:
            data = sock.recv(MAX_REPLY)
        except socket.timeout as exc:
            raise ClamdUnavailable('clamd gave no answer in time') from exc
        except OSError:
            break
        if not data:
            break
        reply += data
        if b'\0' in data or b'\n' in data:
            break
    if not reply.strip(b'\0\r\n '):
        raise ClamdUnavailable('clamd closed the connection without an answer')
    return reply


def _poller(sock):
    poller = select.poll()
    poller.register(sock, select.POLLIN | select.POLLHUP | select.POLLERR)
    return poller


def _early_verdict(sock):
    """clamd's answer before the stream ended (it stops reading past StreamMaxLength):
    a FOUND or an ERROR stands; anything else, even "OK", is a scan_error, since clamd
    has not seen the whole file."""
    verdict = parse_reply(_read_reply(sock))
    if verdict.result == OK:
        return Verdict(ERROR)
    return verdict


def scan_stream(chunks, *, socket_path, timeout=600):
    """Send the bytes from the iterable `chunks` to clamd at `socket_path` and return its
    Verdict. Raises ClamdUnavailable when clamd gives no verdict. `timeout` (seconds)
    bounds every socket operation, the wait for the verdict included."""
    if not socket_path:
        raise ClamdUnavailable('no clamd socket is configured')
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        try:
            sock.connect(socket_path)
            sock.sendall(COMMAND)
        except OSError as exc:
            raise ClamdUnavailable(type(exc).__name__) from exc
        # Errors of the source (`chunks`, e.g. an S3 read) propagate as they are; only the
        # sends are guarded here.
        poller = _poller(sock)
        for chunk in chunks:
            for start in range(0, len(chunk), CHUNK_SIZE):
                if poller.poll(0):
                    return _early_verdict(sock)
                piece = chunk[start:start + CHUNK_SIZE]
                if not _send(sock, struct.pack('!L', len(piece)) + piece):
                    return _early_verdict(sock)
        _send(sock, struct.pack('!L', 0))
        return parse_reply(_read_reply(sock))
    finally:
        sock.close()


def version(*, socket_path, timeout=10):
    """clamd's VERSION answer, e.g. `ClamAV 1.4.3/27450/Fri Nov  6 08:30:00 2026` (engine
    / signature version / signature date). Raises ClamdUnavailable."""
    if not socket_path:
        raise ClamdUnavailable('no clamd socket is configured')
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        try:
            sock.connect(socket_path)
            sock.sendall(b'zVERSION\0')
        except OSError as exc:
            raise ClamdUnavailable(type(exc).__name__) from exc
        return _read_reply(sock).decode('utf-8', 'replace').strip('\0\r\n ')
    finally:
        sock.close()


def signature_date(version_text):
    """The signature date of a VERSION answer (a naive UTC datetime), or None."""
    from datetime import datetime

    parts = version_text.split('/')
    if len(parts) < 3:
        return None
    try:
        return datetime.strptime(' '.join(parts[2].split()), '%a %b %d %H:%M:%S %Y')
    except ValueError:
        return None


def _send(sock, data):
    """Send `data`; False when clamd closed the connection (over StreamMaxLength: its
    reply, if any, is still there to read)."""
    try:
        sock.sendall(data)
    except socket.timeout as exc:
        raise ClamdUnavailable('clamd stopped reading') from exc
    except OSError:
        return False
    return True
