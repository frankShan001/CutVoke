"""The distribution audit must read both native slices and reject corrupt bounds."""
import struct

import pytest

from scripts.verify.verify_unix_macho import read_macho


def image(cpu, minimum, dependency):
    name = dependency.encode() + b'\0'
    size = (24 + len(name) + 7) // 8 * 8
    load = struct.pack('<IIIIII', 0xC, size, 24, 0, 0, 0) + name
    load += b'\0' * (size - len(load))
    build = struct.pack('<IIIIII', 0x32, 24, 1, minimum, 0, 0)
    return struct.pack('<IIIIIIII', 0xFEEDFACF, cpu, 0, 2, 2, len(load + build), 0, 0) + load + build


def test_reads_thin_architecture_deployment_and_loader_reference():
    result, = read_macho(image(0x100000C, 0xD0000, '@rpath/Electron Framework.framework/Electron Framework'))
    assert result['architecture'] == 'arm64'
    assert result['deployment'] == [(13, 0, 0)]
    assert result['dependencies'] == ['@rpath/Electron Framework.framework/Electron Framework']


def test_reads_both_universal_slices_without_using_host_architecture():
    arm = image(0x100000C, 0xB0000, '/usr/lib/libSystem.B.dylib')
    intel = image(0x1000007, 0xA0D00, '/usr/lib/libSystem.B.dylib')
    start = 48
    fat = struct.pack('>II', 0xCAFEBABE, 2)
    fat += struct.pack('>IIIII', 0x100000C, 0, start, len(arm), 0)
    fat += struct.pack('>IIIII', 0x1000007, 0, start + len(arm), len(intel), 0)
    result = read_macho(fat + arm + intel)
    assert [item['architecture'] for item in result] == ['arm64', 'x86_64']
    assert [item['deployment'] for item in result] == [[(11, 0, 0)], [(10, 13, 0)]]


def test_rejects_truncated_images_and_invalid_load_command_sizes():
    binary = image(0x100000C, 0xD0000, '/usr/lib/libSystem.B.dylib')
    with pytest.raises(AssertionError, match='Truncated'):
        read_macho(binary[:-1])
    broken = bytearray(binary)
    struct.pack_into('<I', broken, 36, 0xFFFF)
    with pytest.raises(AssertionError, match='Invalid Mach-O load command'):
        read_macho(broken)
