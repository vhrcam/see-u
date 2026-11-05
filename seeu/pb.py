import struct


def _varint(n):
    out = bytearray()
    while True:
        low, n = n & 0x7F, n >> 7
        out.append(low | 0x80 if n else low)
        if not n:
            return bytes(out)


def num(field, value):
    return _varint(field << 3) + _varint(value)


def blob(field, value):
    data = value.encode() if isinstance(value, str) else value
    return _varint(field << 3 | 2) + _varint(len(data)) + data


def decode(data):
    out, i = {}, 0

    def varint():
        nonlocal i
        value, shift = 0, 0
        while True:
            byte = data[i]
            i += 1
            value |= (byte & 0x7F) << shift
            shift += 7
            if not byte & 0x80:
                return value

    while i < len(data):
        key = varint()
        field, wire = key >> 3, key & 7
        if wire == 0:
            value = varint()
        elif wire == 1:
            value = struct.unpack("<Q", data[i:i + 8])[0]
            i += 8
        elif wire == 2:
            size = varint()
            value = data[i:i + size]
            i += size
        elif wire == 5:
            value = struct.unpack("<I", data[i:i + 4])[0]
            i += 4
        else:
            raise ValueError(f"unknown wire type {wire}")
        out.setdefault(field, []).append(value)
    return out
