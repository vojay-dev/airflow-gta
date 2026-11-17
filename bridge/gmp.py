"""Reads a GTA2 .gmp map (DMAP chunk) into a 256 x 256 grid of what a car can drive on.

Format (from the GTA2 map format notes, as used by the community map tools):
- header "GBMP", u16 version, then chunks of [4-byte type][u32 size][data].
- DMAP: u32 base[256 * 256] (word offset of each column), u32 column_words,
  u32 columns[column_words], u32 num_blocks, block_info blocks[num_blocks].
- A column at columns[base[y * 256 + x]] is: u8 height, u8 offset, u16 pad,
  u32 block index for z = offset .. height - 1.
- block_info: u16 left, right, top, bottom, lid; u8 arrows; u8 slope_type.
  slope_type & 3 is the ground type: 0 air, 1 road, 2 pavement, 3 field.
"""

from __future__ import annotations

import struct

AIR, ROAD, PAVEMENT, FIELD = 0, 1, 2, 3
SIZE = 256


class GtaMap:
    def __init__(self, data: bytes):
        if data[:4] != b"GBMP":
            raise ValueError("not a GTA2 map")
        pos = 6
        chunks = {}
        while pos + 8 <= len(data):
            kind = data[pos : pos + 4].decode("latin-1")
            size = struct.unpack_from("<I", data, pos + 4)[0]
            chunks[kind] = (pos + 8, size)
            pos += 8 + size
        if "DMAP" not in chunks:
            raise ValueError(f"no DMAP chunk, found {list(chunks)}")
        start, _ = chunks["DMAP"]
        base = struct.unpack_from(f"<{SIZE * SIZE}I", data, start)
        p = start + SIZE * SIZE * 4
        column_words = struct.unpack_from("<I", data, p)[0]
        p += 4
        columns = struct.unpack_from(f"<{column_words}I", data, p)
        p += column_words * 4
        num_blocks = struct.unpack_from("<I", data, p)[0]
        p += 4
        # keep only what we need per block: ground type and traffic arrows
        self.block_type = bytearray(num_blocks)
        self.block_arrows = bytearray(num_blocks)
        for i in range(num_blocks):
            arrows, slope = data[p + i * 12 + 10], data[p + i * 12 + 11]
            self.block_arrows[i] = arrows
            self.block_type[i] = slope & 3 if (slope >> 2) == 0 else (slope & 3) | 0x80  # 0x80: sloped
        self.base, self.columns = base, columns

    def block(self, x: int, y: int, z: int) -> int | None:
        """Block index at (x, y, z), or None for empty space."""
        if not (0 <= x < SIZE and 0 <= y < SIZE):
            return None
        off = self.base[y * SIZE + x]
        head = self.columns[off]
        height, offset = head & 0xFF, (head >> 8) & 0xFF
        if not (offset <= z < height):
            return None
        return self.columns[off + 1 + (z - offset)]

    def ground(self, x: int, y: int, z: int) -> int:
        b = self.block(x, y, z)
        return AIR if b is None else self.block_type[b] & 0x7F

    def surface_grid(self, level: int) -> list[list[str]]:
        """What a car at street `level` meets in each column: 'R' road, 'P' pavement, 'F' field, '#' blocked."""
        grid = []
        for y in range(SIZE):
            row = []
            for x in range(SIZE):
                here = self.ground(x, y, level)
                above = self.ground(x, y, level + 1)
                if here in (ROAD, PAVEMENT, FIELD) and above == AIR:
                    row.append({ROAD: "R", PAVEMENT: "P", FIELD: "F"}[here])
                else:
                    row.append("#")
            grid.append(row)
        return grid
