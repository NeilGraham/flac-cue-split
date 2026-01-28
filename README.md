# flac-cue-split

Split single-file FLAC albums into individual tracks using CUE sheets.

**Perfect for Plex users**: Plex and other media servers don't support CUE sheets, so albums ripped as a single FLAC file won't display individual tracks. This tool splits them into separate files with proper metadata so Plex can index each track correctly.

## Features

- Recursively searches directories for FLAC + CUE pairs
- Automatic CUE file encoding detection (UTF-8, Latin-1, Shift-JIS, etc.)
- Skips albums that are already split
- Safe dry-run by default - see what would happen before committing
- Prompts before deleting source files
- Preserves CUE files after splitting

## Requirements

- Python 3.12+
- [ffmpeg](https://ffmpeg.org/download.html) installed and in PATH
    - **Ubuntu/Debian**: `sudo apt install ffmpeg`
    - **macOS**: `brew install ffmpeg`
    - **Windows**: Download from [ffmpeg.org](https://ffmpeg.org/download.html) and add to PATH

## Installation

```bash
# Using uv (recommended)
uv tool install flac-cue-split

# Using pipx
pipx install flac-cue-split

# Using pip
pip install flac-cue-split
```

## Usage

```bash
# Dry run - see what would be split
flac-cue-split /path/to/music

# Actually split the files
flac-cue-split /path/to/music --execute

# Split and delete original FLAC files (prompts for each)
flac-cue-split /path/to/music --execute --delete

# Split and delete without prompts
flac-cue-split /path/to/music --execute --delete --yes

# Delete sources for already-split albums
flac-cue-split /path/to/music --delete --yes
```

### Example Output

```
Found 2 album(s) in Music/

 1. The Elder Scrolls V: Skyrim - Atmospheres
    Jeremy Soule | 2 tracks | 42m 54s
    _Game OST/_Elder Scrolls/Jeremy Soule - Skyrim - Atmospheres/
    2 tracks extracted
    Delete original FLAC? [Y/n]

 2. Dark Side of the Moon
    Pink Floyd | 10 tracks | 42m 59s
    Pink Floyd/1973 - Dark Side of the Moon/
    10 tracks extracted
    Delete original FLAC? [Y/n]

Done
```

### Output Format

Tracks are saved as `{nn}. {track_name}.flac` where the number is zero-padded based on total track count (e.g., `01.` for <100 tracks, `001.` for 100-999, etc).

## Options

| Option | Description |
|--------|-------------|
| `--execute` | Actually perform the split (default is dry-run) |
| `--delete` | Delete original FLAC files after splitting (prompts for confirmation) |
| `--output`, `-o` | Output directory for split files (default: same as source) |
| `--verbose`, `-v` | Show detailed track listings |
| `--yes`, `-y` | Auto-confirm prompts (use with `--delete`) |

## Safety

- **Dry-run by default**: Nothing is modified unless you pass `--execute`
- **Deletion prompts**: `--delete` asks for confirmation unless `--yes` is specified
- **Error protection**: Source files are kept if any extraction errors occur
- **Permanent deletion**: `--delete` permanently removes files (not moved to trash)

## License

MIT
