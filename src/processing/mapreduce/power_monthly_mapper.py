#!/usr/bin/env python3
"""Hadoop Streaming mapper: POWER daily rows -> `city_id|YYYY-MM <tab> remaining 15 daily fields`."""
import sys


def map_line(line):
    """Return (key, value) for a daily row, None for the header; a malformed row fails the job."""
    f = line.rstrip("\n").split(",")
    if f[0] == "city_id":
        return None
    if len(f) != 17 or len(f[1]) != 10:
        raise ValueError(f"malformed daily row: {line!r}")
    return f"{f[0]}|{f[1][:7]}", ",".join(f[2:])


def main():
    for line in sys.stdin:
        out = map_line(line)
        if out:
            print(f"{out[0]}\t{out[1]}")


if __name__ == "__main__":
    main()
