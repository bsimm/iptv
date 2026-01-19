# Jellyfin IPTV EPG Generator

Automatically generates EPG (Electronic Program Guide) data for US IPTV channels from the [iptv-org](https://github.com/iptv-org) repository and creates a filtered playlist containing only channels with EPG data.

## What This Does

This tool:
1. Downloads the US M3U playlist from iptv-org/iptv
2. Extracts all channel IDs from the playlist
3. Matches them with available EPG sources from iptv-org/epg
4. **Filters the playlist to only include channels with EPG data**
5. Generates both a filtered M3U playlist and XMLTV guide file for Jellyfin

## Prerequisites

- **Node.js** (v14 or higher) - Install from [nodejs.org](https://nodejs.org/)
- **Git** - Install from [git-scm.com](https://git-scm.com/)
- **uv** - Install from [docs.astral.sh/uv](https://docs.astral.sh/uv/) (for Python version only)

## Quick Start

### Option 1: Python Script (Recommended)

```bash
uv run generate-epg.py
```

Or make it executable and run directly:
```bash
./generate-epg.py
```

### Option 2: Bash Script

```bash
./generate-epg.sh
```

## Output

The script generates two files in the current directory:
- **`guide.xml`** - XMLTV EPG data for all matched channels
- **`playlist-filtered.m3u`** - M3U playlist containing ONLY channels with EPG data

## Using with Jellyfin

### Method 1: Local File Paths (Recommended)

1. **Set up M3U Playlist:**
   - Open Jellyfin Dashboard → **Live TV**
   - Click **Add** under TV Sources
   - Select **M3U Tuner**
   - Enter file path:
     ```
     file:///path/to/jellyfin-iptv-epg/playlist-filtered.m3u
     ```
   - Save

2. **Set up EPG Data:**
   - Go to **Live TV** → **TV Guide Data Providers**
   - Click **Add** and select **XMLTV**
   - Enter file path:
     ```
     file:///path/to/jellyfin-iptv-epg/guide.xml
     ```
   - Set update interval (24 hours recommended)
   - Save

### Method 2: HTTP Server (Remote Access)

Serve the files over HTTP:

```bash
cd /path/to/jellyfin-iptv-epg
npx serve -p 3000
```

Then in Jellyfin use:
- **M3U URL**: `http://your-server-ip:3000/playlist-filtered.m3u`
- **EPG URL**: `http://your-server-ip:3000/guide.xml`

## Updating EPG Data

Run the script periodically to refresh EPG data:

```bash
# Manual update
uv run generate-epg.py

# Or set up a cron job (Linux/Mac)
crontab -e

# Add this line to update daily at 3 AM:
0 3 * * * cd /path/to/jellyfin-iptv-epg && uv run generate-epg.py
```

## Troubleshooting

### Few or No Channels Matched

If few channels match EPG sources, it means the iptv-org/epg repository doesn't have EPG data for those specific channels. This is common for smaller or regional channels.

**What happens:**
- The script automatically removes channels without EPG data
- Your filtered playlist will only contain channels with program guide information
- If no channels match, the script will exit with a warning

**Solutions:**
- Try a different country's playlist that may have better EPG coverage
- Check the iptv-org/epg repository to see which sources are supported
- Consider using the original unfiltered playlist if you don't need EPG data

### Script Fails During npm install

```bash
# Clear npm cache and try again
cd epg-workspace/epg
rm -rf node_modules
npm cache clean --force
npm install
```

### Git Clone Fails

Ensure you have internet connectivity and git installed:
```bash
git --version
```

### Guide.xml Not Created

Check the EPG generation output for errors. Some sites may fail to scrape, but the script should still generate a guide for successful matches.

## Customization

### Different Country/M3U Playlist

To use a different country's playlist, edit the script and change the `M3U_URL` variable:

```python
# For UK channels
M3U_URL = "https://iptv-org.github.io/iptv/countries/uk.m3u"

# For Canada channels
M3U_URL = "https://iptv-org.github.io/iptv/countries/ca.m3u"

# See all available countries at: https://github.com/iptv-org/iptv
```

### Custom Output Locations

Change the output file paths:

```python
OUTPUT_GUIDE = "/path/to/your/guide.xml"
OUTPUT_PLAYLIST = "/path/to/your/playlist-filtered.m3u"
```

## How It Works

1. **Download M3U**: Fetches the M3U playlist from iptv-org
2. **Parse Channels**: Extracts channel metadata and `tvg-id` attributes
3. **Match with EPG**: Searches through 235+ EPG site configurations
4. **Filter Playlist**: Creates a new M3U with ONLY channels that have EPG data
5. **Generate Config**: Creates `channels.xml` for matched channels
6. **Scrape EPG**: Downloads program data from EPG sources
7. **Output Files**: Generates both filtered M3U playlist and XMLTV guide

## Files Created

```
/path/to/jellyfin-iptv-epg/
├── generate-epg.py          # Python automation script
├── generate-epg.sh          # Bash automation script
├── guide.xml                # Generated EPG data (after running)
├── playlist-filtered.m3u    # Filtered M3U with only EPG channels (after running)
└── epg-workspace/           # Working directory (auto-created)
    └── epg/                 # Cloned iptv-org/epg repository
```

## Resources

- [iptv-org/iptv](https://github.com/iptv-org/iptv) - IPTV channel collections
- [iptv-org/epg](https://github.com/iptv-org/epg) - EPG data sources
- [Jellyfin Documentation](https://jellyfin.org/docs/) - Jellyfin setup guides

## What to Commit to Git

This repository is safe to make public. The `.gitignore` file is configured to exclude:

**Excluded (Do NOT commit):**
- `guide.xml` - Generated EPG file
- `playlist-filtered.m3u` - Generated filtered playlist
- `epg-workspace/` - Working directory with cloned EPG repo
- `*.m3u` - Downloaded playlists
- Node modules and dependencies

**Included (Safe to commit):**
- `generate-epg.py` - Python automation script
- `generate-epg.sh` - Bash automation script
- `README.md` - Documentation
- `.gitignore` - Git exclusions

Before pushing to a public repository, always review:
```bash
git status
git diff
```

## License

This tool is provided as-is for personal use. Refer to the licenses of iptv-org projects for their content.
