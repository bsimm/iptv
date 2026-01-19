#!/bin/bash

# Jellyfin IPTV EPG Generator
# This script automatically generates EPG data for channels in an M3U playlist
# Filters playlist to only include channels with EPG data

set -e

M3U_URL="https://iptv-org.github.io/iptv/countries/us.m3u"
EPG_REPO="https://github.com/iptv-org/epg.git"
WORK_DIR="./epg-workspace"
OUTPUT_GUIDE="./guide.xml"
OUTPUT_PLAYLIST="./playlist-filtered.m3u"

echo "=== Jellyfin IPTV EPG Generator ==="
echo ""

# Create workspace
echo "[1/5] Setting up workspace..."
mkdir -p "$WORK_DIR"
cd "$WORK_DIR"

# Clone EPG repository if not exists
if [ ! -d "epg" ]; then
    echo "[2/5] Cloning EPG repository..."
    git clone "$EPG_REPO" epg
else
    echo "[2/5] Updating EPG repository..."
    cd epg
    git pull
    cd ..
fi

cd epg

# Install dependencies
echo "[3/5] Installing dependencies..."
npm install --silent

# Download M3U file
echo "[4/5] Downloading and parsing M3U playlist..."
curl -s "$M3U_URL" > /tmp/original-playlist.m3u

# Extract tvg-id values
grep -oP 'tvg-id="\K[^"]+' /tmp/original-playlist.m3u | sort -u > /tmp/tvg-ids.txt

total_channels=$(wc -l < /tmp/tvg-ids.txt)
echo "Found $total_channels channels in M3U playlist"
echo ""

# Create channels.xml by matching tvg-ids with available sites
echo '<?xml version="1.0" encoding="UTF-8"?>' > channels.xml
echo '<channels>' >> channels.xml

# Track matched tvg-ids for filtering
> /tmp/matched-tvg-ids.txt

# Search through all site configurations for matching channels
matched=0
while IFS= read -r tvg_id; do
    # Search for this tvg_id in all site channel files
    found=0
    for site_file in sites/*/*.channels.xml; do
        if [ -f "$site_file" ]; then
            # Check if this site has the channel
            if grep -q "xmltv_id=\"$tvg_id\"" "$site_file" 2>/dev/null; then
                # Extract the channel entry
                grep -A 1 "xmltv_id=\"$tvg_id\"" "$site_file" | head -2 >> channels.xml
                echo "$tvg_id" >> /tmp/matched-tvg-ids.txt
                ((matched++))
                found=1
                echo "  ✓ Matched: $tvg_id"
                break
            fi
        fi
    done

    if [ $found -eq 0 ]; then
        echo "  ✗ Removed: $tvg_id (no EPG source)"
    fi
done < /tmp/tvg-ids.txt

echo '</channels>' >> channels.xml

removed=$((total_channels - matched))
echo ""
echo "Matched $matched/$total_channels channels with EPG sources"
echo "Removed $removed channels without EPG"
echo ""

# Create filtered M3U playlist with only matched channels
echo "Creating filtered playlist..."
echo '#EXTM3U' > "../$OUTPUT_PLAYLIST"

# Process M3U file and only include matched channels
while IFS= read -r line; do
    if [[ $line == "#EXTINF:"* ]]; then
        # Extract tvg-id from this line
        current_tvg_id=$(echo "$line" | grep -oP 'tvg-id="\K[^"]+' || echo "")

        if [ -n "$current_tvg_id" ] && grep -q "^${current_tvg_id}$" /tmp/matched-tvg-ids.txt; then
            # This channel is matched, include it
            echo "$line" >> "../$OUTPUT_PLAYLIST"
            include_next=1
        else
            include_next=0
        fi
    elif [[ $line == "#"* ]] && [ $include_next -eq 1 ]; then
        # Include metadata lines for matched channels
        echo "$line" >> "../$OUTPUT_PLAYLIST"
    elif [ $include_next -eq 1 ] && [ -n "$line" ]; then
        # This is the stream URL for a matched channel
        echo "$line" >> "../$OUTPUT_PLAYLIST"
        include_next=0
    fi
done < /tmp/original-playlist.m3u

echo "Filtered playlist saved with $matched channels"
echo ""

# Generate EPG
if [ $matched -gt 0 ]; then
    echo "[5/5] Generating EPG data (this may take a few minutes)..."
    npm run grab -- --channels=channels.xml --output=guide.xml

    # Copy output to parent directory
    cp guide.xml "../$OUTPUT_GUIDE"

    echo ""
    echo "=== SUCCESS ==="
    echo "EPG guide generated: $(realpath "../$OUTPUT_GUIDE")"
    echo "Filtered playlist: $(realpath "../$OUTPUT_PLAYLIST")"
    echo ""
    echo "Use these in Jellyfin:"
    echo "  M3U URL: file://$(realpath "../$OUTPUT_PLAYLIST")"
    echo "  EPG URL: file://$(realpath "../$OUTPUT_GUIDE")"
    echo ""
    echo "Or serve them with: npx serve -p 3000"
    echo "  M3U URL: http://your-server-ip:3000/playlist-filtered.m3u"
    echo "  EPG URL: http://your-server-ip:3000/guide.xml"
else
    echo ""
    echo "=== WARNING ==="
    echo "No channels were matched with EPG sources."
    echo "The EPG repository may not have sources for these channels."
fi
