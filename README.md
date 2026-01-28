# flac-cue-split

Split single-file FLAC albums into individual tracks using CUE sheets.

## Requirements

- Python 3.12+
- [ffmpeg](https://ffmpeg.org/download.html) installed and in PATH

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

# Split and delete original FLAC files
flac-cue-split /path/to/music --execute --delete

# Delete sources for already-split albums (no prompts)
flac-cue-split /path/to/music --delete --yes
```

The tool recursively searches for FLAC + CUE pairs and splits them into individual tracks with proper metadata (title, artist, album, track number).

## Options

| Option | Description |
|--------|-------------|
| `--execute` | Actually perform the split (default is dry-run) |
| `--delete` | Delete original FLAC files after splitting |
| `--output`, `-o` | Output directory for split files |
| `--verbose`, `-v` | Show track listings |
| `--yes`, `-y` | Auto-select default option for prompts |

## License

MIT
