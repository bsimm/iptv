#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.8"
# dependencies = []
# ///
"""
Jellyfin IPTV EPG Generator
Automatically generates EPG data for channels in an M3U playlist
Filters playlist to only include channels with EPG data
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

URLS_FILE = "./urls"
EPG_REPO = "https://github.com/iptv-org/epg.git"
WORK_DIR = "./epg-workspace"
OUTPUT_GUIDE = "./guide.xml"
OUTPUT_PLAYLIST = "./playlist-filtered.m3u"
CACHE_FILE = "./epg-workspace/channel-cache.json"
CACHE_MAX_AGE = 24 * 60 * 60  # 24 hours in seconds
GUIDE_CACHE_MAX_AGE = 12 * 60 * 60  # 12 hours in seconds
MAX_CONNECTIONS = 5  # Number of parallel EPG requests (increase for faster processing)
EPG_DAYS = 1  # Number of days to fetch EPG data for (1-2 recommended)
MAX_CHANNELS = 25  # Maximum number of channels to include (set to 0 for unlimited)

# Reliable CDN domains (higher scores = more reliable)
RELIABLE_DOMAINS = {
    "amagi.tv": 10,
    "uplynk.com": 10,
    "pbs.org": 9,
    "fuelmedia.io": 8,
    "cloudfront.net": 7,
    "tsv2.amagi.tv": 10,
    "cvalley.net": 6,
}

# Must-include channels (always included regardless of score)
PRIORITY_CHANNELS = [
    "CSPAN.us@SD",
    "CSPAN2.us@SD",
    "CSPAN3.us@SD",
]


def run_command(cmd, cwd=None, check=True):
    """Run shell command and return output"""
    result = subprocess.run(
        cmd, shell=True, cwd=cwd, capture_output=True, text=True, check=check
    )
    return result.stdout


def read_urls_file():
    """Read URLs from the urls file. Returns empty list if file is missing or blank."""
    urls_path = Path(URLS_FILE)
    if not urls_path.exists():
        return []
    with open(urls_path, "r", encoding="utf-8") as f:
        return [line.strip() for line in f if line.strip() and not line.strip().startswith("#")]


def fetch_priority_streams_via_gh(priority_channels):
    """Fetch M3U streams for priority channels from iptv-org/iptv via gh CLI.

    Lists all stream files matching each country prefix derived from channel IDs
    (e.g. CSPAN.us → us.m3u, us_tvpass.m3u, us_amagi.m3u, ...) and filters to
    matching channels.
    """
    # Derive country prefixes from channel ID suffixes
    # Strip @variant first: "CSPAN.us@SD" → "CSPAN.us" → prefix "us"
    prefixes = {
        channel_id.split("@")[0].rsplit(".", 1)[-1].lower()
        for channel_id in priority_channels
        if "." in channel_id.split("@")[0]
    }
    if not prefixes:
        print("ERROR: Could not infer country prefixes from PRIORITY_CHANNELS")
        return []

    # List all stream files and filter to those matching our prefixes
    listing = run_command(
        "gh api repos/iptv-org/iptv/contents/streams"
        " --jq '[.[] | {name: .name, download_url: .download_url}]'",
        check=False,
    ).strip()
    if not listing:
        print("ERROR: Could not list iptv-org/iptv/streams via gh")
        return []
    try:
        all_files = json.loads(listing)
    except json.JSONDecodeError:
        print("ERROR: Could not parse iptv-org/iptv/streams listing")
        return []

    def matches_prefix(name):
        stem = name[: -len(".m3u")] if name.endswith(".m3u") else name
        return stem in prefixes or any(stem.startswith(f"{p}_") for p in prefixes)

    country_files = [f for f in all_files if matches_prefix(f["name"])]
    print(f"  Found {len(country_files)} stream file(s) to search")

    priority_ids = {p.lower() for p in priority_channels}
    all_channels = []

    for f in country_files:
        try:
            with urllib.request.urlopen(f["download_url"]) as resp:
                content = resp.read().decode("utf-8")
            matched = [
                ch for ch in parse_m3u(content)
                if (ch.get("tvg_id") or "").lower() in priority_ids
            ]
            if matched:
                print(f"  ✓ {f['name']}: {len(matched)} priority stream(s)")
                all_channels.extend(matched)
        except Exception as e:
            print(f"  ✗ {f['name']}: {e}")

    return all_channels


def calculate_reliability_score(channel):
    """Calculate reliability score for a channel based on various factors"""
    score = 0
    stream_url = channel["stream_url"].lower()
    tvg_id = channel.get("tvg_id", "")

    # Check if it's a priority channel (must-include)
    for priority in PRIORITY_CHANNELS:
        if priority.lower() in tvg_id.lower():
            return 1000  # Very high score to ensure inclusion

    # Score based on CDN domain
    for domain, domain_score in RELIABLE_DOMAINS.items():
        if domain in stream_url:
            score += domain_score
            break

    # Bonus for major networks (based on tvg-id or metadata)
    metadata_text = " ".join(channel["metadata_lines"]).lower()
    major_networks = ["nbc", "cbs", "abc", "fox", "pbs"]
    for network in major_networks:
        if network in metadata_text or network in tvg_id.lower():
            score += 5
            break

    # Bonus for government/legislative channels
    if "legislative" in metadata_text or "government" in metadata_text:
        score += 8

    # Bonus for news channels (typically more reliable)
    if "news" in metadata_text:
        score += 3

    # Bonus for education channels (PBS, etc.)
    if "education" in metadata_text:
        score += 4

    # Penalty for geo-blocked or not 24/7
    if "[geo-blocked]" in metadata_text.lower():
        score -= 3
    if "[not 24/7]" in metadata_text.lower():
        score -= 2

    return score


def parse_m3u(m3u_content):
    """Parse M3U content and extract channel entries with metadata"""
    channels = []
    lines = m3u_content.split("\n")

    i = 0
    while i < len(lines):
        line = lines[i].strip()

        # Look for #EXTINF lines
        if line.startswith("#EXTINF:"):
            # Collect all metadata lines for this channel
            metadata_lines = [line]
            i += 1

            # Get any additional metadata lines (like #EXTVLCOPT)
            while (
                i < len(lines)
                and lines[i].strip().startswith("#")
                and not lines[i].strip().startswith("#EXTINF:")
            ):
                metadata_lines.append(lines[i].strip())
                i += 1

            # Next non-empty line should be the stream URL
            while i < len(lines) and not lines[i].strip():
                i += 1

            if i < len(lines):
                stream_url = lines[i].strip()

                # Extract tvg-id if present
                tvg_id_match = re.search(r'tvg-id="([^"]+)"', metadata_lines[0])
                tvg_id = tvg_id_match.group(1) if tvg_id_match else None

                channels.append(
                    {
                        "tvg_id": tvg_id,
                        "metadata_lines": metadata_lines,
                        "stream_url": stream_url,
                        "full_entry": "\n".join(metadata_lines) + "\n" + stream_url,
                    }
                )

        i += 1

    return channels


def find_channel_in_sites(tvg_id, sites_dir):
    """Search for a channel across all site configurations"""
    sites_path = Path(sites_dir)

    for site_channels_file in sites_path.glob("*/*.channels.xml"):
        try:
            tree = ET.parse(site_channels_file)
            root = tree.getroot()

            # Find channel with matching xmltv_id
            for channel in root.findall(".//channel[@xmltv_id='{}']".format(tvg_id)):
                return channel
        except ET.ParseError:
            continue
        except Exception:
            continue

    return None


def save_channel_cache(matched_channels, channels_root):
    """Save matched channels and XML tree to cache file"""
    cache_data = {
        "timestamp": time.time(),
        "matched_channels": matched_channels,
        "channels_xml": ET.tostring(channels_root, encoding="unicode"),
    }

    cache_path = Path(CACHE_FILE)
    cache_path.parent.mkdir(exist_ok=True)

    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump(cache_data, f, indent=2)

    print(f"Channel cache saved to: {cache_path}")


def load_channel_cache():
    """Load matched channels from cache if it exists and is recent"""
    cache_path = Path(CACHE_FILE)

    if not cache_path.exists():
        return None

    try:
        with open(cache_path, "r", encoding="utf-8") as f:
            cache_data = json.load(f)

        cache_age = time.time() - cache_data["timestamp"]

        if cache_age > CACHE_MAX_AGE:
            print(f"Cache is {cache_age / 3600:.1f} hours old, refreshing...")
            return None

        print(f"Loaded channel cache ({cache_age / 3600:.1f} hours old)")

        # Reconstruct channels_root from XML string
        channels_root = ET.fromstring(cache_data["channels_xml"])

        return {
            "matched_channels": cache_data["matched_channels"],
            "channels_root": channels_root,
        }

    except Exception as e:
        print(f"Failed to load cache: {e}")
        return None


def is_guide_recent():
    """Check if guide.xml exists and is recent enough"""
    guide_path = Path(OUTPUT_GUIDE)

    if not guide_path.exists():
        return False

    guide_age = time.time() - guide_path.stat().st_mtime

    if guide_age > GUIDE_CACHE_MAX_AGE:
        print(
            f"Existing guide.xml is {guide_age / 3600:.1f} hours old, regenerating..."
        )
        return False

    print(f"Using existing guide.xml ({guide_age / 3600:.1f} hours old)")
    return True


def main():
    # Parse command line arguments
    parser = argparse.ArgumentParser(description="Generate EPG data for IPTV channels")
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Force refresh channel cache (ignore existing cache)",
    )
    parser.add_argument(
        "--refresh-epg",
        action="store_true",
        help="Force regenerate EPG data (ignore existing guide.xml)",
    )
    parser.add_argument(
        "--max-connections",
        type=int,
        default=MAX_CONNECTIONS,
        help=f"Number of parallel EPG requests (default: {MAX_CONNECTIONS})",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=EPG_DAYS,
        help=f"Number of days to fetch EPG data for (default: {EPG_DAYS})",
    )
    parser.add_argument(
        "--max-channels",
        type=int,
        default=MAX_CHANNELS,
        help=f"Maximum number of channels to include (default: {MAX_CHANNELS}, 0 = unlimited)",
    )
    parser.add_argument(
        "--priority-only",
        action="store_true",
        help="Include only PRIORITY_CHANNELS in output",
    )
    args = parser.parse_args()

    print("=== Jellyfin IPTV EPG Generator ===\n")

    # Create workspace
    print("[1/6] Setting up workspace...")
    work_path = Path(WORK_DIR)
    work_path.mkdir(exist_ok=True)
    epg_path = work_path / "epg"

    # Clone or update EPG repository
    if not epg_path.exists():
        print("[2/6] Cloning EPG repository...")
        run_command(f"git clone {EPG_REPO} {epg_path}")
    else:
        print("[2/6] Updating EPG repository...")
        run_command("git pull", cwd=epg_path)

    # Install dependencies
    print("[3/6] Installing dependencies...")
    run_command("npm install", cwd=epg_path)

    sites_dir = epg_path / "sites"

    # Try to load from cache
    cache = None if args.refresh else load_channel_cache()

    if cache:
        print("[4/6] Using cached M3U playlist data")
        print("[5/6] Using cached channel matching results\n")
        matched_channels = cache["matched_channels"]
        channels_root = cache["channels_root"]
        matched_count = len(matched_channels)
    else:
        print("[4/6] Downloading M3U playlists...")
        if args.priority_only:
            print("--priority-only: fetching streams from iptv-org/iptv via gh (ignoring urls file)...")
            all_channels = fetch_priority_streams_via_gh(PRIORITY_CHANNELS)
        else:
            m3u_urls = read_urls_file()
            if not m3u_urls:
                print(f"ERROR: No URLs found in {URLS_FILE}")
                sys.exit(1)
            print(f"Found {len(m3u_urls)} playlist URL(s) in {URLS_FILE}")

            all_channels = []
            for idx, m3u_url in enumerate(m3u_urls, 1):
                print(f"  [{idx}/{len(m3u_urls)}] Downloading {m3u_url}...")
                try:
                    with urllib.request.urlopen(m3u_url) as response:
                        m3u_content = response.read().decode("utf-8")

                    playlist_channels = parse_m3u(m3u_content)
                    all_channels.extend(playlist_channels)
                    print(f"       Found {len(playlist_channels)} channels")
                except Exception as e:
                    print(f"       ERROR: Failed to download playlist: {e}")
                    continue

        channels = all_channels
        total_channels = len(channels)
        print(f"\nTotal: {total_channels} channels from all playlists\n")

        # Match channels with EPG sources
        print("[5/6] Matching channels with EPG sources and filtering playlist...")

        channels_root = ET.Element("channels")
        matched_channels = []
        matched_count = 0
        skipped_count = 0

        for channel in channels:
            tvg_id = channel["tvg_id"]

            if not tvg_id:
                print(f"  ⊘ Skipped: No tvg-id in channel")
                skipped_count += 1
                continue

            channel_element = find_channel_in_sites(tvg_id, sites_dir)

            if channel_element is not None:
                channels_root.append(channel_element)
                matched_channels.append(channel)
                matched_count += 1
                print(f"  ✓ Matched: {tvg_id}")
            else:
                print(f"  ✗ Removed: {tvg_id} (no EPG source)")

        print(f"\nMatched {matched_count}/{total_channels} channels with EPG sources")
        print(f"Skipped {skipped_count} channels without tvg-id")
        print(
            f"Removed {total_channels - matched_count - skipped_count} channels without EPG\n"
        )

        # Save to cache
        save_channel_cache(matched_channels, channels_root)

    # Filter to priority channels only if flag is set
    if args.priority_only:
        priority_matched = [
            ch for ch in matched_channels
            if ch.get("tvg_id") in PRIORITY_CHANNELS
        ]
        print(f"[Priority Filter] Keeping {len(priority_matched)}/{len(matched_channels)} priority channels\n")
        matched_channels = priority_matched
        channels_root = ET.Element("channels")
        for channel in matched_channels:
            channel_element = find_channel_in_sites(channel["tvg_id"], sites_dir)
            if channel_element is not None:
                channels_root.append(channel_element)
        matched_count = len(matched_channels)

    # Filter to most reliable channels if max_channels is set (skip when priority_only)
    if not args.priority_only and args.max_channels > 0 and len(matched_channels) > args.max_channels:
        print(f"[Reliability Filter] Selecting top {args.max_channels} most reliable channels...")

        # Calculate reliability scores for all matched channels
        scored_channels = []
        for channel in matched_channels:
            score = calculate_reliability_score(channel)
            scored_channels.append((score, channel))

        # Sort by score (descending) and take top max_channels
        scored_channels.sort(key=lambda x: x[0], reverse=True)
        top_channels = [ch for score, ch in scored_channels[:args.max_channels]]

        # Show what was selected
        print(f"\nTop {args.max_channels} channels selected:")
        for score, channel in scored_channels[:args.max_channels]:
            tvg_id = channel.get("tvg_id", "Unknown")
            print(f"  ✓ {tvg_id} (score: {score})")

        # Update matched_channels and rebuild channels_root
        matched_channels = top_channels
        channels_root = ET.Element("channels")
        for channel in matched_channels:
            channel_element = find_channel_in_sites(channel["tvg_id"], sites_dir)
            if channel_element is not None:
                channels_root.append(channel_element)

        matched_count = len(matched_channels)
        print(f"\nFiltered to {matched_count} channels\n")
    elif not args.priority_only and args.max_channels > 0:
        print(f"[Reliability Filter] All {matched_count} channels included (under limit of {args.max_channels})\n")

    if matched_count == 0:
        print("=== WARNING ===")
        print("No channels were matched with EPG sources.")
        print("The EPG repository may not have sources for these channels.")
        sys.exit(1)

    # Write channels.xml
    channels_file = epg_path / "channels.xml"
    tree = ET.ElementTree(channels_root)
    ET.indent(tree, space="  ")
    tree.write(channels_file, encoding="utf-8", xml_declaration=True)

    # Write filtered M3U playlist
    print("Writing filtered M3U playlist...")
    filtered_playlist_path = Path(OUTPUT_PLAYLIST).resolve()
    with open(filtered_playlist_path, "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        for channel in matched_channels:
            f.write(channel["full_entry"] + "\n")

    print(f"Filtered playlist saved: {filtered_playlist_path}")
    print(f"Contains {len(matched_channels)} channels with EPG data\n")

    # Check if we can skip EPG generation
    if not args.refresh_epg and is_guide_recent():
        print("[6/6] Skipping EPG generation (guide.xml is recent)\n")
        print("=== SUCCESS ===")
        print(f"Filtered playlist: {filtered_playlist_path}")
        print(f"EPG guide: {Path(OUTPUT_GUIDE).resolve()}")
        print(f"\nTo force regenerate EPG, use: --refresh-epg")
        return

    # Generate EPG
    print("[6/6] Generating EPG data...")
    print(
        f"Settings: {args.max_connections} parallel connections, {args.days} day(s) of data"
    )
    print("This may take several minutes...\n")

    try:
        # Set NODE_OPTIONS to increase memory limit for large channel lists
        env = os.environ.copy()
        env["NODE_OPTIONS"] = "--max-old-space-size=8192"

        # Build command with optimization options
        grab_cmd = (
            f"npm run grab -- "
            f"--channels=channels.xml "
            f"--output=guide.xml "
            f"--maxConnections={args.max_connections} "
            f"--days={args.days}"
        )

        print(f"Running: {grab_cmd}\n")

        # Run without capturing output so we can see progress in real-time
        result = subprocess.run(grab_cmd, shell=True, cwd=epg_path, check=True, env=env)

        # Copy output
        output_guide_path = Path(OUTPUT_GUIDE).resolve()
        guide_path = epg_path / "guide.xml"

        if guide_path.exists():
            import shutil

            shutil.copy(guide_path, output_guide_path)

            print("\n=== SUCCESS ===")
            print(f"EPG guide generated: {output_guide_path}")
            print(f"Filtered playlist: {filtered_playlist_path}")
            print("\nFiles are in the repository root - commit and push to GitHub")
            print("Then use these URLs in Jellyfin:")
            print(
                f"  M3U URL: https://raw.githubusercontent.com/YOUR-USERNAME/YOUR-REPO/main/playlist-filtered.m3u"
            )
            print(
                f"  EPG URL: https://raw.githubusercontent.com/YOUR-USERNAME/YOUR-REPO/main/guide.xml"
            )
            print("\nOr for local testing:")
            print(f"  M3U URL: file://{filtered_playlist_path}")
            print(f"  EPG URL: file://{output_guide_path}")
            print("\nPerformance tips:")
            print(
                f"  - Increase speed: --max-connections=10 (current: {args.max_connections})"
            )
            print(f"  - Reduce data: --days=1 (current: {args.days})")
            print(f"  - Skip if recent: guide regenerates only if >12h old")
        else:
            print("\n=== ERROR ===")
            print("EPG generation completed but guide.xml was not created.")

    except subprocess.CalledProcessError as e:
        print(f"\n=== ERROR ===")
        print(f"EPG generation failed with exit code {e.returncode}")
        print("Check the output above for error details")
        sys.exit(1)


if __name__ == "__main__":
    main()
