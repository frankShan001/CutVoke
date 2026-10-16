"""Read Mach-O architecture, deployment and loader paths without host SDK tools."""
import struct

MACH_MAGICS = {b'\xcf\xfa\xed\xfe', b'\xce\xfa\xed\xfe', b'\xca\xfe\xba\xbe',
               b'\xbe\xba\xfe\xca', b'\xca\xfe\xba\xbf', b'\xbf\xba\xfe\xca'}

def read_macho(data):
    """Return one entry per Mach-O slice; reject malformed command boundaries."""
    magic = bytes(data[:4])
    if magic in {b'\xca\xfe\xba\xbe', b'\xbe\xba\xfe\xca', b'\xca\xfe\xba\xbf', b'\xbf\xba\xfe\xca'}:
        endian = '>' if magic in {b'\xca\xfe\xba\xbe', b'\xca\xfe\xba\xbf'} else '<'
        wide = magic in {b'\xca\xfe\xba\xbf', b'\xbf\xba\xfe\xca'}
        count, = struct.unpack_from(endian + 'I', data, 4)
        entries = []
        for index in range(count):
            fields = struct.unpack_from(endian + ('IIQQII' if wide else 'IIIII'), data, 8 + index * (32 if wide else 20))
            offset, size = fields[2:4]
            assert offset + size <= len(data), 'Truncated universal image'
            entries += read_macho(data[offset:offset + size])
        return entries
    assert magic in {b'\xcf\xfa\xed\xfe', b'\xce\xfa\xed\xfe'}, 'Unknown Mach-O header'
    cpu, = struct.unpack_from('<I', data, 4)
    count, command_bytes = struct.unpack_from('<II', data, 16)
    offset = 32 if magic == b'\xcf\xfa\xed\xfe' else 28
    end = offset + command_bytes
    assert end <= len(data), 'Truncated Mach-O commands'
    item = {'architecture': {0x1000007: 'x86_64', 0x100000C: 'arm64'}.get(cpu, hex(cpu)),
            'deployment': [], 'dependencies': [], 'rpaths': []}
    for _ in range(count):
        command, size = struct.unpack_from('<II', data, offset)
        assert size >= 8 and offset + size <= end, 'Invalid Mach-O load command'
        if command in {0xC, 0xD, 0x80000018, 0x8000001F, 0x20, 0x80000023, 0x8000001C}:
            string_offset, = struct.unpack_from('<I', data, offset + 8)
            assert 8 <= string_offset < size
            value = data[offset + string_offset:offset + size].split(b'\0', 1)[0].decode('utf-8')
            item['rpaths' if command == 0x8000001C else 'dependencies'].append(value)
        if command in {0x24, 0x32}:
            value, = struct.unpack_from('<I', data, offset + (8 if command == 0x24 else 12))
            item['deployment'].append((value >> 16, (value >> 8) & 255, value & 255))
        offset += size
    assert offset == end, 'Mach-O command count does not match its size'
    return [item]
