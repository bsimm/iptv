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

M3U_URL = "https://iptv-org.github.io/iptv/countries/us.m3u"
EPG_REPO = "https://github.com/iptv-org/epg.git"
WORK_DIR = "./epg-workspace"
OUTPUT_GUIDE = "./guide.xml"
OUTPUT_PLAYLIST = "./playlist-filtered.m3u"
CACHE_FILE = "./epg-workspace/channel-cache.json"
CACHE_MAX_AGE = 24 * 60 * 60  # 24 hours in seconds
GUIDE_CACHE_MAX_AGE = 12 * 60 * 60  # 12 hours in seconds
MAX_CONNECTIONS = 5  # Number of parallel EPG requests (increase for faster processing)
EPG_DAYS = 1  # Number of days to fetch EPG data for (1-2 recommended)


def run_command(cmd, cwd=None, check=True):
    """Run shell command and return output"""
    result = subprocess.run(
        cmd,
        shell=True,
        cwd=cwd,
        capture_output=True,
        text=True,
        check=check
    )
    return result.stdout


def parse_m3u(m3u_content):
    """Parse M3U content and extract channel entries with metadata"""
    channels = []
    lines = m3u_content.split('\n')

    i = 0
    while i < len(lines):
        line = lines[i].strip()

        # Look for #EXTINF lines
        if line.startswith('#EXTINF:'):
            # Collect all metadata lines for this channel
            metadata_lines = [line]
            i += 1

            # Get any additional metadata lines (like #EXTVLCOPT)
            while i < len(lines) and lines[i].strip().startswith('#') and not lines[i].strip().startswith('#EXTINF:'):
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

                channels.append({
                    'tvg_id': tvg_id,
                    'metadata_lines': metadata_lines,
                    'stream_url': stream_url,
                    'full_entry': '\n'.join(metadata_lines) + '\n' + stream_url
                })

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
        'timestamp': time.time(),
        'matched_channels': matched_channels,
        'channels_xml': ET.tostring(channels_root, encoding='unicode')
    }

    cache_path = Path(CACHE_FILE)
    cache_path.parent.mkdir(exist_ok=True)

    with open(cache_path, 'w', encoding='utf-8') as f:
        json.dump(cache_data, f, indent=2)

    print(f"Channel cache saved to: {cache_path}")


def load_channel_cache():
    """Load matched channels from cache if it exists and is recent"""
    cache_path = Path(CACHE_FILE)

    if not cache_path.exists():
        return None

    try:
        with open(cache_path, 'r', encoding='utf-8') as f:
            cache_data = json.load(f)

        cache_age = time.time() - cache_data['timestamp']

        if cache_age > CACHE_MAX_AGE:
            print(f"Cache is {cache_age / 3600:.1f} hours old, refreshing...")
            return None

        print(f"Loaded channel cache ({cache_age / 3600:.1f} hours old)")

        # Reconstruct channels_root from XML string
        channels_root = ET.fromstring(cache_data['channels_xml'])

        return {
            'matched_channels': cache_data['matched_channels'],
            'channels_root': channels_root
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
        print(f"Existing guide.xml is {guide_age / 3600:.1f} hours old, regenerating...")
        return False

    print(f"Using existing guide.xml ({guide_age / 3600:.1f} hours old)")
    return True


def main():
    # Parse command line arguments
    parser = argparse.ArgumentParser(description='Generate EPG data for IPTV channels')
    parser.add_argument('--refresh', action='store_true',
                       help='Force refresh channel cache (ignore existing cache)')
    parser.add_argument('--refresh-epg', action='store_true',
                       help='Force regenerate EPG data (ignore existing guide.xml)')
    parser.add_argument('--max-connections', type=int, default=MAX_CONNECTIONS,
                       help=f'Number of parallel EPG requests (default: {MAX_CONNECTIONS})')
    parser.add_argument('--days', type=int, default=EPG_DAYS,
                       help=f'Number of days to fetch EPG data for (default: {EPG_DAYS})')
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

    # Try to load from cache
    cache = None if args.refresh else load_channel_cache()

    if cache:
        print("[4/6] Using cached M3U playlist data")
        print("[5/6] Using cached channel matching results\n")
        matched_channels = cache['matched_channels']
        channels_root = cache['channels_root']
        matched_count = len(matched_channels)
    else:
        # Download and parse M3U
        print("[4/6] Downloading M3U playlist...")
        with urllib.request.urlopen(M3U_URL) as response:
            m3u_content = response.read().decode('utf-8')

        channels = parse_m3u(m3u_content)
        total_channels = len(channels)
        print(f"Found {total_channels} channels in M3U playlist\n")

        # Match channels with EPG sources
        print("[5/6] Matching channels with EPG sources and filtering playlist...")

        channels_root = ET.Element("channels")
        matched_channels = []
        matched_count = 0
        skipped_count = 0

        sites_dir = epg_path / "sites"

        for channel in channels:
            tvg_id = channel['tvg_id']

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
        print(f"Removed {total_channels - matched_count - skipped_count} channels without EPG\n")

        # Save to cache
        save_channel_cache(matched_channels, channels_root)

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
    with open(filtered_playlist_path, 'w', encoding='utf-8') as f:
        f.write("#EXTM3U\n")
        for channel in matched_channels:
            f.write(channel['full_entry'] + '\n')

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
    print(f"Settings: {args.max_connections} parallel connections, {args.days} day(s) of data")
    print("This may take several minutes...\n")

    try:
        # Set NODE_OPTIONS to increase memory limit for large channel lists
        env = os.environ.copy()
        env['NODE_OPTIONS'] = '--max-old-space-size=8192'

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
        result = subprocess.run(
            grab_cmd,
            shell=True,
            cwd=epg_path,
            check=True,
            env=env
        )

        # Copy output
        output_guide_path = Path(OUTPUT_GUIDE).resolve()
        guide_path = epg_path / "guide.xml"

        if guide_path.exists():
            import shutil
            shutil.copy(guide_path, output_guide_path)

            print("\n=== SUCCESS ===")
            print(f"EPG guide generated: {output_guide_path}")
            print(f"Filtered playlist: {filtered_playlist_path}")
            print("\nUse these in Jellyfin:")
            print(f"  M3U URL: file://{filtered_playlist_path}")
            print(f"  EPG URL: file://{output_guide_path}")
            print("\nOr serve them with: npx serve -p 3000")
            print(f"  M3U URL: http://your-server-ip:3000/playlist-filtered.m3u")
            print(f"  EPG URL: http://your-server-ip:3000/guide.xml")
            print("\nPerformance tips:")
            print(f"  - Increase speed: --max-connections=10 (current: {args.max_connections})")
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
