#!/usr/bin/env python3
"""
FLAC + CUE Splitter

Recursively searches for FLAC + CUE file pairs and splits the FLAC files
into individual tracks based on the CUE sheet information.

Requires: ffmpeg to be installed and available in PATH
"""

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn
from rich.theme import Theme

theme = Theme({
    "album": "bold cyan",
    "artist": "dim",
    "info": "dim",
    "folder": "blue",
    "done": "green",
    "pending": "yellow",
    "error": "bold red",
})
console = Console(theme=theme)


@dataclass
class Track:
    """Represents a single track from a CUE sheet."""
    number: int
    title: str
    performer: str
    start_time: str  # MM:SS:FF format (frames are 1/75 second)

    def start_seconds(self) -> float:
        """Convert CUE timestamp to seconds."""
        parts = self.start_time.split(":")
        minutes = int(parts[0])
        seconds = int(parts[1])
        frames = int(parts[2]) if len(parts) > 2 else 0
        return minutes * 60 + seconds + frames / 75.0

    def safe_filename(self, total_tracks: int = 99) -> str:
        """Generate a safe filename for this track."""
        # Remove or replace characters that are problematic in filenames
        safe_title = re.sub(r'[<>:"/\\|?*]', '_', self.title)
        safe_title = safe_title.strip('. ')
        # Pad track number based on total tracks (minimum 2 digits)
        width = max(2, len(str(total_tracks)))
        return f"{self.number:0{width}d}. {safe_title}.flac"


@dataclass
class CueSheet:
    """Represents a parsed CUE sheet."""
    album_title: str
    album_performer: str
    file_name: str
    tracks: list[Track]


def parse_cue_file(cue_path: Path) -> CueSheet | None:
    """Parse a CUE file and extract track information."""
    try:
        # Try different encodings commonly used in CUE files
        content = None
        for encoding in ['utf-8', 'utf-8-sig', 'latin-1', 'cp1252', 'shift-jis']:
            try:
                content = cue_path.read_text(encoding=encoding)
                break
            except UnicodeDecodeError:
                continue

        if content is None:
            console.print(f"[error]Warning: Could not decode {cue_path}[/]")
            return None

    except OSError as e:
        console.print(f"[error]Warning: Could not read {cue_path}: {e}[/]")
        return None

    album_title = ""
    album_performer = ""
    file_name = ""
    tracks: list[Track] = []

    current_track_num = 0
    current_track_title = ""
    current_track_performer = ""
    current_track_index = ""
    in_track = False

    # Patterns for parsing
    title_pattern = re.compile(r'TITLE\s+"([^"]*)"', re.IGNORECASE)
    performer_pattern = re.compile(r'PERFORMER\s+"([^"]*)"', re.IGNORECASE)
    file_pattern = re.compile(r'FILE\s+"([^"]+)"\s+\w+', re.IGNORECASE)
    track_pattern = re.compile(r'TRACK\s+(\d+)\s+AUDIO', re.IGNORECASE)
    index_pattern = re.compile(r'INDEX\s+01\s+(\d+:\d+:\d+)', re.IGNORECASE)

    for line in content.splitlines():
        line = line.strip()

        # Check for FILE (before any TRACK)
        file_match = file_pattern.match(line)
        if file_match and not in_track:
            file_name = file_match.group(1)
            continue

        # Check for TRACK
        track_match = track_pattern.match(line)
        if track_match:
            # Save previous track if exists
            if in_track and current_track_index:
                tracks.append(Track(
                    number=current_track_num,
                    title=current_track_title or f"Track {current_track_num}",
                    performer=current_track_performer or album_performer,
                    start_time=current_track_index
                ))

            in_track = True
            current_track_num = int(track_match.group(1))
            current_track_title = ""
            current_track_performer = ""
            current_track_index = ""
            continue

        # Check for TITLE
        title_match = title_pattern.search(line)
        if title_match:
            if in_track:
                current_track_title = title_match.group(1)
            else:
                album_title = title_match.group(1)
            continue

        # Check for PERFORMER
        performer_match = performer_pattern.search(line)
        if performer_match:
            if in_track:
                current_track_performer = performer_match.group(1)
            else:
                album_performer = performer_match.group(1)
            continue

        # Check for INDEX 01
        index_match = index_pattern.search(line)
        if index_match and in_track:
            current_track_index = index_match.group(1)
            continue

    # Don't forget the last track
    if in_track and current_track_index:
        tracks.append(Track(
            number=current_track_num,
            title=current_track_title or f"Track {current_track_num}",
            performer=current_track_performer or album_performer,
            start_time=current_track_index
        ))

    if not tracks:
        return None

    return CueSheet(
        album_title=album_title,
        album_performer=album_performer,
        file_name=file_name,
        tracks=tracks
    )


def looks_like_track_file(filename: str) -> bool:
    """Check if a FLAC filename looks like an individual track (vs full album)."""
    # Common track naming patterns: "01.", "01 -", "Track 01", etc.
    track_patterns = [
        r'^\d{1,2}\.',  # Starts with 1-2 digits and a period
        r'^\d{1,2}\s*-',  # Starts with 1-2 digits and a dash
        r'^Track\s*\d+',  # Starts with "Track" followed by number
        r'^\d{1,2}\s+\w+',  # Starts with 1-2 digits and space then word
    ]
    for pattern in track_patterns:
        if re.match(pattern, filename, re.IGNORECASE):
            return True
    return False


def find_flac_cue_pairs(directory: Path) -> list[tuple[Path, Path]]:
    """Recursively find all FLAC + CUE file pairs in a directory."""
    pairs: list[tuple[Path, Path]] = []

    # Group CUE files by directory
    cue_by_dir: dict[Path, list[Path]] = {}
    for cue_path in directory.rglob("*.cue"):
        cue_dir = cue_path.parent
        if cue_dir not in cue_by_dir:
            cue_by_dir[cue_dir] = []
        cue_by_dir[cue_dir].append(cue_path)

    # Process each directory
    for cue_dir, cue_files in cue_by_dir.items():
        # If multiple CUE files, prioritize ones with 'flac' in filename
        if len(cue_files) > 1:
            flac_cues = [c for c in cue_files if 'flac' in c.name.lower()]
            if flac_cues:
                cue_files = flac_cues
            else:
                # Warn and use first
                try:
                    rel_path = cue_dir.resolve().relative_to(directory.resolve())
                except ValueError:
                    rel_path = cue_dir.name
                cue_names = ", ".join(c.name for c in cue_files)
                console.print(f"[info]Warning: Multiple CUE files in '{rel_path}/'[/]")
                console.print(f"[info]  Found: {cue_names}[/]")
                console.print(f"[info]  Using: {cue_files[0].name}[/]")
                cue_files = [cue_files[0]]

        # Find FLAC files in directory, excluding files that look like individual tracks
        all_flac_files = list(cue_dir.glob("*.flac")) + list(cue_dir.glob("*.FLAC"))
        album_flac_files = [f for f in all_flac_files if not looks_like_track_file(f.name)]

        for cue_path in cue_files:
            cue_stem = cue_path.stem
            flac_path = None

            # First try exact name match (CUE and FLAC have same stem)
            # Skip if it looks like an individual track file
            exact_match = cue_dir / f"{cue_stem}.flac"
            if exact_match.exists() and not looks_like_track_file(exact_match.name):
                flac_path = exact_match
            else:
                exact_match = cue_dir / f"{cue_stem}.FLAC"
                if exact_match.exists() and not looks_like_track_file(exact_match.name):
                    flac_path = exact_match

            # If no exact match, try to find from CUE content
            # Skip if the referenced file looks like an individual track
            if not flac_path:
                cue_sheet = parse_cue_file(cue_path)
                if cue_sheet and cue_sheet.file_name:
                    referenced_flac = cue_dir / cue_sheet.file_name
                    if referenced_flac.exists() and not looks_like_track_file(referenced_flac.name):
                        flac_path = referenced_flac

            # If still no match, look for album FLAC whose stem is contained in the CUE filename
            # Only consider files that don't look like individual tracks
            if not flac_path and album_flac_files:
                cue_name_lower = cue_path.name.lower()
                for flac_file in album_flac_files:
                    flac_stem_lower = flac_file.stem.lower()
                    # Check if FLAC stem is contained in CUE filename
                    if flac_stem_lower in cue_name_lower:
                        flac_path = flac_file
                        break

            if flac_path:
                pairs.append((flac_path, cue_path))

    return pairs


def is_already_split(cue_sheet: CueSheet, output_dir: Path) -> bool:
    """Check if all expected track files already exist in the output directory."""
    if not output_dir.exists():
        return False
    total_tracks = len(cue_sheet.tracks)
    for track in cue_sheet.tracks:
        expected_file = output_dir / track.safe_filename(total_tracks)
        if not expected_file.exists():
            return False
    return True


def check_ffmpeg() -> bool:
    """Check if ffmpeg is available."""
    try:
        result = subprocess.run(
            ["ffmpeg", "-version"],
            capture_output=True,
            text=True
        )
        return result.returncode == 0
    except FileNotFoundError:
        return False


def get_flac_duration(flac_path: Path) -> float | None:
    """Get the duration of a FLAC file in seconds using ffprobe."""
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                str(flac_path)
            ],
            capture_output=True,
            text=True
        )
        if result.returncode == 0 and result.stdout.strip():
            return float(result.stdout.strip())
    except (FileNotFoundError, ValueError):
        pass
    return None


def split_flac(
    flac_path: Path,
    cue_sheet: CueSheet,
    output_dir: Path,
    execute: bool = False,
    progress: Progress | None = None,
    task_id: int | None = None,
) -> tuple[int, int]:
    """
    Split a FLAC file into individual tracks.

    Returns (success_count, error_count).
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    tracks = cue_sheet.tracks
    total_tracks = len(tracks)
    success = 0
    errors = 0

    for i, track in enumerate(tracks):
        output_file = output_dir / track.safe_filename(total_tracks)
        start_time = track.start_seconds()

        # Determine end time (start of next track, or end of file)
        if i + 1 < len(tracks):
            end_time = tracks[i + 1].start_seconds()
            duration = end_time - start_time
            duration_args = ["-t", f"{duration:.3f}"]
        else:
            duration_args = []

        cmd = [
            "ffmpeg",
            "-i", str(flac_path),
            "-ss", f"{start_time:.3f}",
            *duration_args,
            "-c:a", "flac",
            "-compression_level", "8",
            "-metadata", f"title={track.title}",
            "-metadata", f"artist={track.performer}",
            "-metadata", f"album={cue_sheet.album_title}",
            "-metadata", f"track={track.number}/{len(tracks)}",
            "-y",
            str(output_file)
        ]

        if execute:
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode != 0:
                errors += 1
            else:
                success += 1

        if progress and task_id is not None:
            progress.update(task_id, advance=1)

    return success, errors


def path_arg(value: str) -> Path:
    """Convert path string to Path, handling Git Bash /c/... style paths."""
    if len(value) >= 3 and value[0] == '/' and value[2] == '/':
        value = f"{value[1].upper()}:{value[2:]}"
    return Path(value).expanduser()


def format_duration_seconds(total_secs: float) -> str:
    """Format seconds as human-readable duration."""
    total_secs = int(total_secs)
    if total_secs < 60:
        return f"{total_secs}s"
    mins, secs = divmod(total_secs, 60)
    if mins >= 60:
        hrs, mins = divmod(mins, 60)
        return f"{hrs}h {mins}m {secs}s"
    return f"{mins}m {secs}s"


def format_duration(tracks: list[Track], total_duration: float | None = None) -> str:
    """Format total duration. Uses actual duration if provided, otherwise estimates from last track."""
    if total_duration is not None:
        return format_duration_seconds(total_duration)
    if not tracks:
        return "?"
    last = tracks[-1]
    total_secs = int(last.start_seconds())
    if total_secs < 60:
        return "?"
    return "~" + format_duration_seconds(total_secs)


def relative_path(path: Path, base: Path) -> str:
    """Get relative path string, or just the name if not under base."""
    try:
        return str(path.resolve().relative_to(base.resolve()))
    except ValueError:
        return path.name


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Split FLAC files based on CUE sheets",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s /path/to/music                   # Dry run - show what would be done
  %(prog)s /path/to/music --execute         # Actually split the files
  %(prog)s . --execute --delete             # Split and delete originals
  %(prog)s . --delete --yes                 # Delete already-split sources (no prompts)
        """
    )
    parser.add_argument(
        "directory",
        type=path_arg,
        help="Directory to search for FLAC + CUE pairs"
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Actually perform the split (default is dry-run)"
    )
    parser.add_argument(
        "--output", "-o",
        type=path_arg,
        default=None,
        help="Output directory for split files (default: same as source)"
    )
    parser.add_argument(
        "--delete",
        action="store_true",
        help="Delete original FLAC files after splitting (keeps .cue files)"
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Show track listings"
    )
    parser.add_argument(
        "--yes", "-y",
        action="store_true",
        help="Auto-select default option for prompts"
    )

    args = parser.parse_args()

    # Validate directory
    if not args.directory.exists():
        console.print(f"[error]Error:[/] Directory does not exist: {args.directory}")
        return 1

    if not args.directory.is_dir():
        console.print(f"[error]Error:[/] Not a directory: {args.directory}")
        return 1

    # Check for ffmpeg
    if not check_ffmpeg():
        console.print("[error]Error:[/] ffmpeg is not installed or not in PATH")
        console.print("[info]Install: https://ffmpeg.org/download.html[/]")
        return 1

    # Find pairs
    with console.status("[info]Scanning for albums...[/]", spinner="dots"):
        pairs = find_flac_cue_pairs(args.directory)

    if not pairs:
        console.print("[info]No FLAC + CUE pairs found.[/]")
        return 0

    base_dir = args.directory.resolve()

    # Build album info
    albums = []
    for flac_path, cue_path in pairs:
        cue_sheet = parse_cue_file(cue_path)
        if args.output:
            try:
                rel = flac_path.parent.resolve().relative_to(base_dir)
                output_dir = args.output.resolve() / rel
            except ValueError:
                output_dir = args.output.resolve()
        else:
            output_dir = flac_path.parent
        already_split = is_already_split(cue_sheet, output_dir) if cue_sheet else False
        albums.append((flac_path, cue_path, cue_sheet, output_dir, already_split))

    pending = sum(1 for *_, done in albums if not done)
    done_count = len(albums) - pending

    # Header
    console.print()
    console.print(f"[bold]Found {len(albums)} album(s)[/] in [folder]{base_dir.name}/[/]")
    if done_count > 0 and not args.execute:
        console.print(f"[done]{done_count} already split[/], [pending]{pending} pending[/]")
    console.print()

    # Display albums
    for i, (flac_path, cue_path, cue_sheet, output_dir, already_split) in enumerate(albums, 1):
        folder = relative_path(flac_path.parent, base_dir)

        if not cue_sheet:
            console.print(f"[info]{i:2}.[/] [folder]{folder}/[/]")
            console.print(f"    [error]Could not parse CUE file, skipping[/]")
            console.print()
            continue

        album = cue_sheet.album_title or "(unknown album)"
        artist = cue_sheet.album_performer or "(unknown artist)"
        n_tracks = len(cue_sheet.tracks)

        # Get actual FLAC duration for accurate album + last track duration
        flac_duration = get_flac_duration(flac_path)
        duration = format_duration(cue_sheet.tracks, flac_duration)

        # Use green styling for already-split albums
        if already_split and not args.execute:
            console.print(f"[done]{i:2}. {album}[/]")
            console.print(f"    [done]{artist} | {n_tracks} tracks | {duration}[/]")
            console.print(f"    [done]{folder}/[/]")
        else:
            console.print(f"[info]{i:2}.[/] [album]{album}[/]")
            console.print(f"    [artist]{artist}[/] [info]|[/] {n_tracks} tracks [info]|[/] {duration}")
            console.print(f"    [folder]{folder}/[/]")

        if args.verbose:
            console.print(f"    [info]FLAC: {flac_path.name}[/]")
            console.print(f"    [info]CUE:  {cue_path.name}[/]")
            tracks = cue_sheet.tracks
            # Calculate max lengths for alignment
            max_title_len = max(len(t.title) for t in tracks)
            max_time_len = max(len(t.start_time) for t in tracks)
            for j, track in enumerate(tracks):
                # Calculate track duration
                if j + 1 < len(tracks):
                    duration_secs = tracks[j + 1].start_seconds() - track.start_seconds()
                    track_dur = format_duration_seconds(duration_secs)
                elif flac_duration is not None:
                    # Last track: use FLAC duration to calculate
                    duration_secs = flac_duration - track.start_seconds()
                    track_dur = format_duration_seconds(duration_secs)
                else:
                    track_dur = "?"
                padded_title = track.title.ljust(max_title_len)
                padded_time = track.start_time.rjust(max_time_len)
                console.print(f"        [info]{track.number:2}.[/] {padded_title}  [info]{padded_time}  {track_dur:>8}[/]")

        # Track if split had errors (to prevent deletion on failure)
        split_had_errors = False

        if args.execute and not already_split:
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                BarColumn(bar_width=20),
                TaskProgressColumn(),
                console=console,
                transient=True,
            ) as progress:
                task = progress.add_task("Splitting", total=n_tracks)
                success, errors = split_flac(flac_path, cue_sheet, output_dir, execute=True, progress=progress, task_id=task)
            if errors:
                split_had_errors = True
                console.print(f"    [done]{success} tracks[/] [error]({errors} errors)[/]")
            else:
                console.print(f"    [done]{n_tracks} tracks extracted[/]")
        elif args.execute and already_split:
            console.print(f"    [info]Already split, skipping[/]")

        # Handle --delete
        if args.delete and flac_path.exists():
            if args.execute:
                if split_had_errors:
                    console.print(f"    [error]Source kept due to extraction errors[/]")
                else:
                    # Default is Y (delete) for successfully split albums
                    if args.yes:
                        response = 'y'
                    else:
                        response = console.input("    Delete original FLAC? [Y/n] ").strip().lower()
                    if response != 'n':
                        flac_path.unlink()
                        console.print(f"    [info]Source deleted[/]")
            elif already_split:
                # Default is Y (delete) for already-split albums
                if args.yes:
                    response = 'y'
                else:
                    response = console.input("    Delete original FLAC? [Y/n] ").strip().lower()
                if response != 'n':
                    flac_path.unlink()
                    console.print(f"    [done]Deleted[/]")
            else:
                # Default is N (keep) for not-yet-split albums
                if args.yes:
                    response = 'n'
                    console.print(f"    [info]Not yet split, keeping source[/]")
                else:
                    console.print(f"    [pending]Not yet split[/]")
                    response = console.input("    Delete anyway? [y/N] ").strip().lower()
                    if response == 'y':
                        flac_path.unlink()
                        console.print(f"    [done]Deleted[/]")

        console.print()

    # Footer
    if not args.execute:
        if pending > 0:
            console.print(f"[info]Dry run complete.[/] Run with [bold]--execute[/] to split {pending} album(s).")
        else:
            console.print("[done]All albums already split.[/]")
    else:
        console.print("[done]Done.[/]")

    return 0


if __name__ == "__main__":
    sys.exit(main())
