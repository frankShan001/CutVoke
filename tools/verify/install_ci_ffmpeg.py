"""Bounded canonical-source installation; probe bytes are NOT installation evidence.

Run after an authenticated, fail-on-any-error apt update, inside the workflow's
9-minute process-group deadline. Only task-local source/index copies are changed.
The final apt update/install performs normal Release/Packages/deb authentication.
"""
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import tempfile
from urllib.parse import urlsplit

MIRROR_URI = 'mirror+file:/etc/apt/apt-mirrors.txt'
PROBE_BYTES = 1024 * 1024


class _ProbeTimeout(ValueError):
    """Curl reported its bounded transfer timeout (exit 28)."""


def candidates(text):
    urls, seen = [], set()
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith('#'):
            continue
        url = line.split()[0]
        parsed = urlsplit(url)
        # Exact hosts from actions/runner-images configure-apt-sources.sh.
        official = parsed.hostname in ('archive.ubuntu.com', 'azure.archive.ubuntu.com',
                                       'us-east-2.ec2.archive.ubuntu.com', 'security.ubuntu.com')
        if (not official or parsed.scheme not in ('http', 'https') or parsed.port is not None
                or parsed.username or parsed.password or parsed.path != '/ubuntu/'
                or parsed.query or parsed.fragment or parsed.hostname in seen):
            raise ValueError('unsupported or duplicate runner mirror')
        seen.add(parsed.hostname)
        # Recognize the configured archive candidates; select() probes canonical
        # only. Preserve all suites including noble-security below.
        if parsed.hostname != 'security.ubuntu.com':
            urls.append(url)
    if not 1 <= len(urls) <= 2:
        raise ValueError('expected one or two configured official archive candidates')
    return urls


def package_metadata(text):
    fields = dict(line.split(': ', 1) for line in text.splitlines() if ': ' in line and not line.startswith(' '))
    filename = fields.get('Filename', '')
    path = PurePosixPath(filename)
    if (fields.get('Package') != 'ffmpeg' or fields.get('Architecture') != 'amd64'
            or not re.fullmatch(r'(?:\d+:)?6\.1(?:[.~-].*)?', fields.get('Version', ''))
            or not re.fullmatch(r'pool/universe/f/ffmpeg/ffmpeg_[A-Za-z0-9.+~_-]+_amd64\.deb', filename)
            or '..' in path.parts or not re.fullmatch(r'[0-9a-f]{64}', fields.get('SHA256', ''))
            or not PROBE_BYTES <= int(fields.get('Size', '0')) <= 20 * 1024 * 1024):
        raise ValueError('unexpected signed-index FFmpeg metadata')
    return filename, int(fields['Size'])


def probe(url, filename, size, directory, run=subprocess.run):
    # A timed-out request can leave partial headers/body. Each attempt must
    # qualify on its own response, even when the enclosing task dir is reused.
    with tempfile.TemporaryDirectory(prefix='qualification-', dir=directory) as temp:
        body, headers = Path(temp) / 'probe.deb.part', Path(temp) / 'probe.headers'
        result = run(['curl', '--disable', '--fail', '--silent', '--show-error',
                      '--proto', '=http,https', '--max-redirs', '0', '--connect-timeout', '5',
                      '--max-time', '15', '--range', f'0-{PROBE_BYTES - 1}',
                      '--max-filesize', str(PROBE_BYTES), '--output', str(body),
                      '--dump-header', str(headers), '--write-out', '%{http_code} %{time_total} %{time_starttransfer}',
                      url + filename], capture_output=True, text=True, timeout=17)
        if result.returncode == 28:
            raise _ProbeTimeout('bounded probe failed (28)')
        if result.returncode:
            raise ValueError(f'bounded probe failed ({result.returncode})')
        status, elapsed, *timing = result.stdout.split(maxsplit=2)
        if (status != '206' or not body.is_file() or body.stat().st_size != PROBE_BYTES
                or not re.search(rf'^content-range:\s*bytes 0-{PROBE_BYTES - 1}/{size}\s*$',
                                 headers.read_text(), re.I | re.M)):
            raise ValueError('probe range/size mismatch or redirect')
        seconds = float(elapsed)
        qualified = 0 < seconds <= 4
        elapsed_ms = ttfb_ms = None
        if 0 < seconds <= 15:
            elapsed_ms = round(seconds * 1000)
            # Only curl's decimal timing is informational. Never print the token
            # or use it to qualify the transfer or decide a retry.
            if timing and re.fullmatch(r'[0-9]{1,2}(?:\.[0-9]{1,6})?', timing[0]):
                first_byte = float(timing[0])
                if first_byte <= seconds:
                    ttfb_ms = round(first_byte * 1000)
        print(json.dumps({'probe': 'qualified-range', 'outcome': 'pass' if qualified else 'below-floor',
                          'elapsed_ms': elapsed_ms, 'ttfb_ms': ttfb_ms}), flush=True)
        if not qualified:
            raise ValueError('probe below 256 KiB/s qualification floor')
        return seconds


def select(urls, filename, size, directory, probe_fn=probe):
    # Fixed policy, not a speed contest: small ranges did not predict full
    # dependency throughput. Only use this source if the runner configured it.
    canonical = 'https://archive.ubuntu.com/ubuntu/'
    if canonical not in urls:
        raise RuntimeError('Configured HTTPS canonical archive missing; installation not attempted')
    try:
        try:
            elapsed = probe_fn(canonical, filename, size, directory)
        except _ProbeTimeout:
            # Retry only curl's transient timeout on this same configured URL.
            # Both attempts are covered by the existing process-group deadline.
            print(json.dumps({'mirror': canonical, 'probe': 'retry', 'attempt': 2,
                              'curlExit': 28}), flush=True)
            elapsed = probe_fn(canonical, filename, size, directory)
    except (ValueError, OSError, subprocess.TimeoutExpired) as exc:
        print(json.dumps({'mirror': canonical, 'probe': 'failed', 'reason': str(exc)}), flush=True)
        raise RuntimeError('Canonical transfer qualification failed; no fallback or installation') from exc
    print(json.dumps({'mirror': canonical, 'bytes': PROBE_BYTES, 'seconds': elapsed,
                      'qualification': 'partial transfer only, not complete installation evidence'}), flush=True)
    return canonical


def source_copy(etc, target, selected):
    # Fail closed on unfamiliar runner layouts rather than broad text rewrites.
    original = etc / 'sources.list'
    if original.exists() and any(l.strip() and not l.lstrip().startswith('#') for l in original.read_text().splitlines()):
        raise ValueError('expected deb822-only Ubuntu runner sources')
    parts = etc / 'sources.list.d'
    ubuntu = parts / 'ubuntu.sources'
    text = ubuntu.read_text()
    if MIRROR_URI not in text:
        raise ValueError('expected runner mirror URI')
    for block in re.split(r'\n\s*\n', text.strip()):
        active = '\n'.join(line for line in block.splitlines() if not line.lstrip().startswith('#'))
        if not active.strip():
            continue
        if (not re.search(r'^Signed-By: /usr/share/keyrings/ubuntu-archive-keyring\.gpg\s*$', active, re.M)
                or re.search(r'^(Trusted|Allow-Insecure|Allow-Weak|Allow-Downgrade-To-Insecure):', active, re.M | re.I)):
            raise ValueError('unexpected Ubuntu source authentication/layout')
    # Exact URI token replacement; suites, components and Signed-By unchanged.
    for line in text.splitlines():
        if MIRROR_URI in line and (not line.startswith('URIs: ') or line.split()[1:] != [MIRROR_URI]):
            raise ValueError('unsupported mirror URI placement')
    (target / 'sources.list').write_text('')
    copied = target / 'sources.list.d'
    copied.mkdir()
    for file in parts.iterdir():
        if file.suffix in ('.list', '.sources'):
            contents = file.read_text()
            if file != ubuntu and MIRROR_URI in contents:
                raise ValueError('unexpected additional mirror source')
            (copied / file.name).write_text(contents.replace(MIRROR_URI, selected) if file == ubuntu else contents)
    lists = target / 'lists'
    (lists / 'partial').mkdir(parents=True)
    target.chmod(0o755)
    return ['-o', f'Dir::Etc::sourcelist={target / "sources.list"}',
            '-o', f'Dir::Etc::sourceparts={copied}', '-o', f'Dir::State::lists={lists}']


def main():
    run = subprocess.run
    def command(args, timeout=60):
        return run(args, check=True, text=True, capture_output=True, timeout=timeout).stdout
    urls = candidates(Path('/etc/apt/apt-mirrors.txt').read_text())
    policy = command(['apt-cache', 'policy', 'ffmpeg'])
    print(policy, flush=True)
    match = re.search(r'^\s*Candidate:\s*(\S+)$', policy, re.M)
    if not match:
        raise ValueError('no FFmpeg candidate')
    metadata = command(['apt-cache', 'show', '--no-all-versions', f'ffmpeg={match[1]}'])
    filename, size = package_metadata(metadata)
    print(command(['apt-get', '--simulate', 'install', '-y', 'ffmpeg']), flush=True)
    with tempfile.TemporaryDirectory(prefix='cutvoke-apt-') as temp:
        root = Path(temp)
        selected = select(urls, filename, size, root)
        options = source_copy(Path('/etc/apt'), root, selected)
        options += ['-o', 'Acquire::Retries=1', '-o', 'Acquire::http::Timeout=30',
                    '-o', 'Acquire::https::Timeout=30', '-o', 'APT::Update::Error-Mode=any']
        print(json.dumps({'selected': selected, 'probePackage': filename,
                          'authentication': 'APT Release/Packages/deb validation, no overrides'}), flush=True)
        # No fallback install loop. Any signature, update, solver or install error stops.
        # Only the workflow's process-group deadline terminates APT. An inner
        # direct-child timeout could orphan acquire/dpkg subprocesses.
        run(['apt-get', *options, 'update'], check=True)
        run(['apt-get', *options, 'install', '-y', 'ffmpeg'], check=True)


if __name__ == '__main__':
    main()
