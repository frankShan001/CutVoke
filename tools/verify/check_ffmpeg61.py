"""Require both Ubuntu CI media tools to report the expected 6.1 series."""
import re
import subprocess


def verify(tool, output):
    first = output.splitlines()[0] if output else ''
    if not re.match(rf'^{re.escape(tool)} version 6\.1(?:[.\s-]|$)', first):
        raise ValueError(f'{tool}: expected 6.1 series, got {first!r}')


def main():
    for tool in ('ffmpeg', 'ffprobe'):
        result = subprocess.run([tool, '-version'], check=True, capture_output=True, text=True, timeout=15)
        print(result.stdout, end='')
        verify(tool, result.stdout)


if __name__ == '__main__':
    main()
