"""MODIS MOD11A2 v061 (8-day 1 km land surface temperature) - search, download, HDF4 reading.

  python src/ingestion/modis.py plan                         keyless CMR search: granule counts and sizes per tile (no download)
  python src/ingestion/modis.py check                        prerequisites: credentials present (not shown), pyhdf, HDFS, free disk
  python src/ingestion/modis.py pilot --tile h24v05 --year 2015 --count 2
                                                             download + validate + upload a few granules (needs Earthdata credentials)

Credentials come only from environment variables / the git-ignored .env: EARTHDATA_TOKEN (bearer) or
EARTHDATA_USERNAME + EARTHDATA_PASSWORD. They are sent only to *.earthdata.nasa.gov hosts, never printed or stored.
Nothing is downloaded without credentials, and the satellite values are never invented.
"""
import argparse
import base64
import hashlib
import http.cookiejar
import json
import math
import os
import re
import shutil
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
CMR = "https://cmr.earthdata.nasa.gov/search/granules.json"
SHORT_NAME, VERSION = "MOD11A2", "061"
TILES = ["h24v06", "h24v05", "h23v05"]
SOURCE = "modis_mod11a2_satellite"
HDF4_MAGIC = b"\x0e\x03\x13\x01"
R, TILE_M, PIX = 6371007.181, 1111950.5196666666, 1111950.5196666666 / 1200
X0, Y0 = -20015109.354, 10007554.677
LST_SCALE, QC_FILL = 0.02, 0


class ModisError(Exception):
    pass


# ---------- geometry (sinusoidal grid) ----------
def tile_pixel(lat, lon):
    """MODIS sinusoidal tile (h, v) and the row/col of the 1 km pixel containing a point."""
    x = R * math.radians(lon) * math.cos(math.radians(lat))
    y = R * math.radians(lat)
    gx, gy = (x - X0) / PIX, (Y0 - y) / PIX
    h, v = int(gx // 1200), int(gy // 1200)
    return h, v, int(gy - v * 1200), int(gx - h * 1200)


# ---------- CMR search (keyless) ----------
def search(tile, start, end):
    """All MOD11A2 v061 granules of one tile between two ISO dates: [{name, url, size_mb, time_start}]."""
    out, page = [], 1
    while True:
        query = urllib.parse.urlencode({
            "short_name": SHORT_NAME, "version": VERSION, "temporal": f"{start}T00:00:00Z,{end}T23:59:59Z",
            "producer_granule_id": f"*.{tile}.*", "options[producer_granule_id][pattern]": "true",
            "page_size": 2000, "page_num": page})
        with urllib.request.urlopen(f"{CMR}?{query}", timeout=60) as r:
            entries = json.loads(r.read())["feed"]["entry"]
        for e in entries:
            href = next((l["href"] for l in e.get("links", []) if l.get("rel", "").endswith("/data#") and l["href"].endswith(".hdf")), None)
            if href:
                out.append({"name": href.rsplit("/", 1)[1], "url": href, "size_mb": float(e.get("granule_size", 0)), "time_start": e["time_start"]})
        if len(entries) < 2000:
            return out
        page += 1


def plan(start="2015-01-01", end="2025-12-31"):
    rows = {t: search(t, start, end) for t in TILES}
    return {t: {"granules": len(g), "size_mb": round(sum(x["size_mb"] for x in g), 1), "first": g[0]["time_start"] if g else None,
                "last": g[-1]["time_start"] if g else None} for t, g in rows.items()}


# ---------- authenticated download ----------
class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def credentials():
    token = os.environ.get("EARTHDATA_TOKEN", "").strip()
    user, pw = os.environ.get("EARTHDATA_USERNAME", "").strip(), os.environ.get("EARTHDATA_PASSWORD", "")
    if token:
        return f"Bearer {token}"
    if user and pw:
        return "Basic " + base64.b64encode(f"{user}:{pw}".encode()).decode()
    raise ModisError("NASA Earthdata credentials are not configured (EARTHDATA_TOKEN or EARTHDATA_USERNAME/EARTHDATA_PASSWORD)")


def download(url, dest, auth, max_hops=10):
    """Follow Earthdata redirects by hand so the Authorization header only goes to *.earthdata.nasa.gov hosts."""
    opener = urllib.request.build_opener(_NoRedirect, urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    for _ in range(max_hops):
        host = urllib.parse.urlparse(url).hostname or ""
        req = urllib.request.Request(url, headers={"Authorization": auth} if host.endswith("earthdata.nasa.gov") else {})
        try:
            with opener.open(req, timeout=120) as r, open(dest, "wb") as f:
                while chunk := r.read(1 << 20):
                    f.write(chunk)
                return
        except urllib.error.HTTPError as e:
            if e.code in (301, 302, 303, 307, 308) and e.headers.get("Location"):
                url = urllib.parse.urljoin(url, e.headers["Location"])
                continue
            raise ModisError(f"download failed: HTTP {e.code}")
    raise ModisError("too many redirects")


def validate_hdf(path, size_mb_hint=None):
    with open(path, "rb") as f:
        head = f.read(4)
    size = Path(path).stat().st_size
    if head != HDF4_MAGIC:
        raise ModisError("file is not HDF4 (magic bytes differ); likely a login or error page")
    if size_mb_hint and abs(size / 1e6 - size_mb_hint) > max(0.3, 0.1 * size_mb_hint):
        raise ModisError(f"size {size / 1e6:.2f} MB differs from the catalogue {size_mb_hint} MB")
    return size


# ---------- HDF4 reading ----------
def check_geolocation(sd, h, v):
    """The HDF-EOS StructMetadata upper-left corner must equal the standard sinusoidal corner of tile (h, v)."""
    text = sd.attributes().get("StructMetadata.0", "")
    m = re.search(r"UpperLeftPointMtrs=\(\s*(-?[0-9.]+)\s*,\s*(-?[0-9.]+)\s*\)", text)
    if not m:
        raise ModisError("no HDF-EOS UpperLeftPointMtrs in StructMetadata.0")
    x, y = float(m.group(1)), float(m.group(2))
    if abs(x - (X0 + h * TILE_M)) > 1 or abs(y - (Y0 - v * TILE_M)) > 1:
        raise ModisError(f"granule corner ({x}, {y}) is not the corner of tile h{h:02d}v{v:02d}")


def read_lst(path, lat, lon, half=1):
    """Day/night LST (K) and QC for the (2*half+1)^2 pixel window around a point.

    Scale factor and fill value come from the file's own SDS attributes. A pixel counts only if LST is not the fill value
    and the mandatory QC flag (bits 0-1) says LST was produced (0 good, 1 other quality; 2 cloud and 3 other = not produced).
    Nothing is gap-filled; a window without valid pixels gives None.
    """
    from pyhdf.SD import SD, SDC   # imported here: only needed when real HDF4 files exist
    h, v, row, col = tile_pixel(lat, lon)
    sd = SD(str(path), SDC.READ)
    try:
        check_geolocation(sd, h, v)
        out = {"tile_h": h, "tile_v": v, "row": row, "col": col, "window": f"{2 * half + 1}x{2 * half + 1}"}
        window = (slice(row - half, row + half + 1), slice(col - half, col + half + 1))
        for part in ("Day", "Night"):
            lst_sds, qc_sds = sd.select(f"LST_{part}_1km"), sd.select(f"QC_{part}")
            attrs = lst_sds.attributes()
            scale, fill = float(attrs.get("scale_factor", LST_SCALE)), int(attrs.get("_FillValue", 0))
            lst, qc = lst_sds[window], qc_sds[window]
            pairs = [(int(x), int(q) & 3) for x, q in zip(lst.flatten(), qc.flatten())]
            valid = [x * scale for x, flag in pairs if x != fill and flag in (0, 1)]
            key = part.lower()
            out[f"lst_{key}_k"] = round(sum(valid) / len(valid), 2) if valid else None
            out[f"lst_{key}_valid_pixels"] = len(valid)
            out[f"lst_{key}_good_quality_pixels"] = sum(1 for x, flag in pairs if x != fill and flag == 0)
            out[f"lst_{key}_cloud_pixels"] = sum(1 for _, flag in pairs if flag == 2)
            out[f"lst_{key}_scale_factor"] = scale
        return out
    finally:
        sd.end()


def prerequisites(hdfs_tools):
    """Report (without printing any secret) whether the pilot can run."""
    try:
        credentials()
        creds = True
    except ModisError:
        creds = False
    try:
        import pyhdf.SD  # noqa: F401
        pyhdf_ok = True
    except ImportError:
        pyhdf_ok = False
    return {"earthdata_credentials_configured": creds, "pyhdf_importable": pyhdf_ok,
            "hdfs_reachable": hdfs_tools.hdfs("-test", "-d", hdfs_tools.HDFS_ROOT).returncode == 0,
            "free_disk_gb": round(shutil.disk_usage(ROOT).free / 1e9, 1)}


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("plan")
    sub.add_parser("check")
    pilot = sub.add_parser("pilot")
    pilot.add_argument("--tile", required=True, choices=TILES)
    pilot.add_argument("--year", type=int, required=True)
    pilot.add_argument("--count", type=int, default=2)
    a = p.parse_args(argv)
    if a.cmd == "plan":
        print(json.dumps(plan(), indent=1))
        return
    from ingestion import nasa_power as hdfs_tools
    if a.cmd == "check":
        print(json.dumps(prerequisites(hdfs_tools), indent=1))
        return
    auth = credentials()
    staging = ROOT / "data" / "staging" / SOURCE
    staging.mkdir(parents=True, exist_ok=True)
    for g in search(a.tile, f"{a.year}-01-01", f"{a.year}-12-31")[: a.count]:
        part = staging / (g["name"] + ".part")
        download(g["url"], part, auth)
        size = validate_hdf(part, g["size_mb"])
        cities = json.loads((ROOT / "config" / "cities.json").read_text(encoding="utf-8"))
        samples = {c: read_lst(part, v["latitude"], v["longitude"]) for c, v in cities.items()
                   if "h%02dv%02d" % tile_pixel(v["latitude"], v["longitude"])[:2] == a.tile}
        print(json.dumps({"granule": g["name"], "verified_city_windows": samples}))
        sha = hdfs_tools.sha256_of(part)
        final = f"{hdfs_tools.HDFS_ROOT}/raw/{SOURCE}/tile={a.tile}/year={a.year}/{g['name']}"
        if hdfs_tools.hdfs_exists(final):
            print(f"exists, not overwritten: {g['name']}")
            part.unlink()
            continue
        tmp = f"{hdfs_tools.HDFS_ROOT}/_tmp/{g['name']}.{sha[:12]}"
        hdfs_tools.hdfs_ok("-mkdir", "-p", f"{hdfs_tools.HDFS_ROOT}/_tmp", final.rsplit("/", 1)[0])
        with open(part, "rb") as f:
            hdfs_tools.hdfs_ok("-put", "-f", "-", tmp, stdin=f)
        if hdfs_tools.hdfs_size(tmp) != size:
            raise ModisError("HDFS copy size differs")
        hdfs_tools.hdfs_ok("-mv", tmp, final)
        manifest = f"{hdfs_tools.HDFS_ROOT}/_manifest/{SOURCE}.jsonl"
        if not hdfs_tools.hdfs_exists(manifest):
            hdfs_tools.hdfs_ok("-touchz", manifest)
        rec = {"object_key": f"{SOURCE}/{g['name']}", "hdfs_path": final, "size_bytes": size, "sha256": sha, "tile": a.tile,
               "time_start": g["time_start"], "status": "ok"}
        hdfs_tools.hdfs("-appendToFile", "-", manifest, input=(json.dumps(rec) + "\n").encode())
        part.unlink()
        print(json.dumps(rec))


if __name__ == "__main__":
    try:
        main()
    except ModisError as e:
        sys.exit(f"MODIS: {e}")
