"""Hadoop Streaming mapper: POWER INTERIM hourly rows -> `city_id|date <tab> six climate values`"""
import sys

HEADER_START = "timestamp_utc"


def map_line(line):
    """Return (key, value) for a data row, None for the header; a malformed row fails the job"""
    f = line.rstrip("\n").split(",")
    if f[0] == HEADER_START:
        return None
    if len(f) != 9 or len(f[0]) != 20 or not f[0].endswith(":00:00Z"):
        raise ValueError(f"malformed INTERIM row: {line!r}")
    return f"{f[1]}|{f[0][:10]}", ",".join(f[2:8])


def main():
    for line in sys.stdin:
        out = map_line(line)
        if out:
            print(f"{out[0]}\t{out[1]}")


if __name__ == "__main__":
    main()
