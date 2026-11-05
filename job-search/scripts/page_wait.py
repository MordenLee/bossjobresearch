"""Bounded DOM readiness polling. A loading/error shell is never an empty result."""
import time


class PageNotReady(TimeoutError):
    def __init__(self, status, snapshot, elapsed, observations):
        self.status, self.snapshot = status, snapshot
        self.elapsed, self.observations = elapsed, observations
        super().__init__(f'{status}: DOM not ready after {elapsed:.1f}s')


def page_state(snapshot):
    text = str(snapshot.get('text') or '').lower()
    if snapshot.get('challenge') or any(s in text for s in (
        'verify you are human', 'verify that you are human', '请完成安全验证',
        '请先完成验证', '确认您是真人', 'checking your browser')):
        return 'CHALLENGE'
    if snapshot.get('login_wall'):
        return 'LOGIN_REQUIRED'
    if any(s in text for s in ('access denied', 'too many requests')):
        return 'ACCESS_DENIED'
    if any(s in text for s in ('workday is currently unavailable', 'experiencing a service interruption')):
        return 'SERVICE_UNAVAILABLE'
    if any(s in text for s in ("the page you are looking for doesn't exist", 'job is no longer available')):
        return 'NOT_FOUND'
    if not text.strip():
        return 'EMPTY_SHELL'
    return 'LOADING_OR_UNVERIFIED'


def wait_page(reader, valid, *, timeout=120, interval=3, stable_seconds=6,
              fingerprint=None, progress=None, clock=time.monotonic, sleep=time.sleep):
    """Only valid, stable site-specific content succeeds. Human gates stop immediately.

    Error pages get the full wait window to recover. Retries/navigation belong to
    the caller; neither this function nor a timeout implies a closed job/zero jobs.
    Reader errors other than known navigation-context loss are not hidden.
    """
    if timeout <= 0 or interval <= 0 or stable_seconds < 0 or stable_seconds >= timeout:
        raise ValueError('Invalid readiness timing')
    start = clock(); previous = None; since = start; snapshot = {}; observations = []
    fingerprint = fingerprint or (lambda s: s)
    while clock() - start < timeout:
        try:
            snapshot = reader() or {}
        except Exception as exc:
            if type(exc).__name__ not in ('ContextLostError', 'DocumentNotFoundError'):
                raise
            snapshot = {'text': '', 'navigation_error': type(exc).__name__}
        status = page_state(snapshot)
        elapsed = clock() - start
        if not observations or observations[-1]['status'] != status:
            observations.append({'status': status, 'elapsed': round(elapsed, 2)})
        if progress:
            progress(status, elapsed, snapshot)
        if status in ('CHALLENGE', 'LOGIN_REQUIRED', 'ACCESS_DENIED'):
            raise PageNotReady(status, snapshot, elapsed, observations)
        # Error text takes precedence over incidental footer/related-job content.
        if status not in ('EMPTY_SHELL', 'SERVICE_UNAVAILABLE', 'NOT_FOUND') and valid(snapshot):
            key = fingerprint(snapshot)
            if key != previous:
                previous, since = key, clock()
            elif clock() - since >= stable_seconds:
                return snapshot
        else:
            previous, since = None, clock()
        sleep(min(interval, max(0, timeout - (clock() - start))))
    raise PageNotReady(page_state(snapshot), snapshot, clock() - start, observations)
