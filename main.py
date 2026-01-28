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
from rich.table import Table
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

    def safe_filename(self) -> str:
        """Generate a safe filename for this track."""
        # Remove or replace characters that are problematic in filenames
        safe_title = re.sub(r'[<>:"/\\|?*]', '_', self.title)
        safe_title = safe_title.strip('. ')
        return f"{self.number:02d} - {safe_title}.flac"


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
            print(f"  WARNING: Could not decode {cue_path}")
            return None

    except OSError as e:
        print(f"  WARNING: Could not read {cue_path}: {e}")
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


def find_flac_cue_pairs(directory: Path) -> list[tuple[Path, Path]]:
    """Recursively find all FLAC + CUE file pairs in a directory."""
    pairs: list[tuple[Path, Path]] = []

    # Find all CUE files
    for cue_path in directory.rglob("*.cue"):
        cue_dir = cue_path.parent
        cue_stem = cue_path.stem

        # Look for matching FLAC file (same name or referenced in CUE)
        flac_candidates = [
            cue_dir / f"{cue_stem}.flac",
            cue_dir / f"{cue_stem}.FLAC",
        ]

        # Also check all FLAC files in the same directory
        flac_files = list(cue_dir.glob("*.flac")) + list(cue_dir.glob("*.FLAC"))

        flac_path = None

        # First try exact name match
        for candidate in flac_candidates:
            if candidate.exists():
                flac_path = candidate
                break

        # If no exact match, try to find from CUE content
        if not flac_path:
            cue_sheet = parse_cue_file(cue_path)
            if cue_sheet and cue_sheet.file_name:
                referenced_flac = cue_dir / cue_sheet.file_name
                if referenced_flac.exists():
                    flac_path = referenced_flac

        # If still no match and there's only one FLAC in the directory, use it
        if not flac_path and len(flac_files) == 1:
            flac_path = flac_files[0]

        if flac_path:
            pairs.append((flac_path, cue_path))

    return pairs


def is_already_split(cue_sheet: CueSheet, output_dir: Path) -> bool:
    """Check if all expected track files already exist in the output directory."""
    if not output_dir.exists():
        return False
    for track in cue_sheet.tracks:
        expected_file = output_dir / track.safe_filename()
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
    success = 0
    errors = 0

    for i, track in enumerate(tracks):
        output_file = output_dir / track.safe_filename()
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


def format_duration(tracks: list[Track]) -> str:
    """Estimate total duration from last track start time."""
    if not tracks:
        return "?"
    last = tracks[-1]
    total_secs = int(last.start_seconds())
    if total_secs < 60:
        return "?"
    mins, secs = divmod(total_secs, 60)
    if mins >= 60:
        hrs, mins = divmod(mins, 60)
        return f"{hrs}h {mins}m {secs}s"
    return f"{mins}m {secs}s"


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
        duration = format_duration(cue_sheet.tracks)

        # Use green styling for already-split albums
        if already_split and not args.execute:
            console.print(f"[done]{i:2}. {album}[/]")
            console.print(f"    [done]{artist} | {n_tracks} tracks | ~{duration}[/]")
            console.print(f"    [done]{folder}/[/]")
        else:
            console.print(f"[info]{i:2}.[/] [album]{album}[/]")
            console.print(f"    [artist]{artist}[/] [info]|[/] {n_tracks} tracks [info]|[/] ~{duration}")
            console.print(f"    [folder]{folder}/[/]")

        if args.verbose:
            tracks = cue_sheet.tracks
            for j, track in enumerate(tracks):
                # Calculate track duration
                if j + 1 < len(tracks):
                    duration_secs = int(tracks[j + 1].start_seconds() - track.start_seconds())
                    dur_m, dur_s = divmod(duration_secs, 60)
                    track_dur = f"{dur_m}m {dur_s}s"
                else:
                    track_dur = "?"
                console.print(f"        [info]{track.number:2}.[/] {track.title} [info]| {track.start_time} | {track_dur}[/]")

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
                console.print(f"    [done]{success} tracks[/] [error]({errors} errors)[/]")
            else:
                console.print(f"    [done]{n_tracks} tracks extracted[/]")
        elif args.execute and already_split:
            console.print(f"    [info]Already split, skipping[/]")

        # Handle --delete
        if args.delete and flac_path.exists():
            if args.execute:
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
