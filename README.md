# Yogurt - A Granola Notes Exporter

Yogurt is a Python script that exports your [Granola](https://granola.ai) AI-enhanced meeting summaries to organized Markdown files with full metadata. Designed to run nightly via macOS `launchd`.

## How It Works

Granola records meetings, blends your notes with the transcript, and generates AI summaries ("panels"). This script:

1. Reads Granola's local cache (`~/Library/Application Support/Granola/cache-v6.json`) for document metadata
2. Fetches AI-generated panel summaries from the Granola API for each document
3. Falls back to the user's raw notes if no AI summary exists (with a note in the file)
4. Skips documents with no content at all

Authentication is handled automatically using the tokens Granola stores locally in `supabase.json`. The script refreshes expired tokens as needed.

> **Important:** The script reads whatever is in the cache at runtime. Granola's free tier restricts UI access to notes older than 14 days, but the data often persists in the cache file regardless. Once a note has been exported to disk, it stays there even if Granola later evicts it from the cache.

## Example Output Structure

```
<your-chosen-directory>/
├── 2026/
│   ├── 01/
│   │   ├── 2026-01-07-monthly-board-meeting.md
│   │   ├── 2026-01-12-john-weekly-check-in.md
│   │   └── 2026-01-22-brain-dump.md
│   ├── 02/
│   │   └── 2026-02-07-monthly-board-meeting.md
│   └── 03/
│       ├── 2026-03-07-monthly-board-meeting.md
│       └── 2026-03-16-john-maria-sync.md
└── .export-state.json   ← change-detection state (do not delete)
```

Each exported file has a markdown header followed by the AI-generated summary:

```markdown
# Monthly Board Meeting

**Time: 14:00**
**Date: 10-03-2026**
**Attendees: Jane Smith, Bob Jones**

### Key Discussion Points
- Item from the AI-generated summary
- ...
```

Documents without an AI summary fall back to raw user notes and include a notice:

```markdown
> **Note:** No Granola summary found. Showing raw user notes.
```

## Setup

### Install

Clone the repo and run the install script:

```bash
git clone https://github.com/nateJDXN/yogurt.git
cd yogurt
./install.sh
```

The installer will walk you through setup:

1. **Output directory** — You'll be asked where to save your exported notes. Press Enter to use the default (`~/Documents/granola-notes`), or enter a custom path. If the directory doesn't exist, you'll be offered the option to create it.
2. **Initial export** — The installer detects your existing Granola notes and shows an estimated export size. You can choose to export everything immediately or do it later.

Once complete, a `launchd` agent is registered to run the export automatically at 2:00 AM daily. If your Mac is asleep at that time, it runs when the machine next wakes up. If the Granola API is unreachable (no internet, server down), the script retries every hour for up to 24 hours.

### Test it

Preview what would be exported without writing anything:

```bash
python3 ~/scripts/granola-export.py --dry-run
```

Run an actual export:

```bash
python3 ~/scripts/granola-export.py
```

Run with verbose logging to see per-document details:

```bash
python3 ~/scripts/granola-export.py --dry-run -v
```

Manually trigger the scheduled job outside its normal schedule:

```bash
launchctl kickstart "gui/$(id -u)/local.granola-export"
```

### Uninstall

```bash
./uninstall.sh
```

This removes the script and launchd agent. Your exported notes are left untouched.

### Logs

The launchd agent captures stdout and stderr to log files:

```
~/Library/Logs/granola-export.log        # stdout (summary output)
~/Library/Logs/granola-export.error.log  # stderr (detailed logs + errors)
```

**Checking recent logs:**

```bash
# View the last export's log output
cat ~/Library/Logs/granola-export.error.log

# Follow logs in real time (useful when triggering manually)
tail -f ~/Library/Logs/granola-export.error.log

# Search for errors
grep ERROR ~/Library/Logs/granola-export.error.log

# Search for API retry attempts
grep "retrying in" ~/Library/Logs/granola-export.error.log
```

> **Note:** launchd overwrites these log files on each run. To preserve history, consider redirecting to a log with rotation, or check the system log: `log show --predicate 'processImagePath CONTAINS "granola-export"' --last 24h`.

## Usage

```
usage: granola-export.py [-h] [-c CACHE] [-o OUTPUT] [-s SUPABASE]
                         [--dry-run] [--force] [--max-retries N] [-v]

Export Granola meeting notes to Markdown files.

options:
  -h, --help            show this help message and exit
  -c CACHE, --cache CACHE
                        Path to Granola cache file
                        (default: ~/Library/Application Support/Granola/cache-v6.json)
  -o OUTPUT, --output OUTPUT
                        Output directory (default: set during install)
  -s SUPABASE, --supabase SUPABASE
                        Path to Granola supabase.json for API auth
                        (default: ~/Library/Application Support/Granola/supabase.json)
  --dry-run             Preview what would be exported without writing files
  --force               Re-export all notes, ignoring change detection
  --max-retries N       Max hourly retries on API failure (default: 23)
  -v, --verbose         Enable debug logging
```

## Incremental Exports

The script tracks a SHA-256 hash of each exported file's content in `.export-state.json` inside the output directory. On subsequent runs, only new or changed notes are written. Use `--force` to bypass this and re-export everything.

## Caveats

- **No dependencies** — uses only the Python 3 standard library.
- **Granola API** — AI summaries are fetched via Granola's private API using locally stored auth tokens. If Granola changes their API, the script will need updating.
- **Cache format may change** — this reads `cache-v6.json`. If Granola ships an update that changes the cache schema or filename (e.g. `cache-v7.json`), the script will need updating.
- **Cache completeness** — the script can only export documents present in the cache. If you haven't opened a note in Granola recently, it may not be in the cache. Running Granola periodically ensures the cache stays populated.
- **Privacy mode** — notes recorded with privacy mode enabled often have no AI summary and empty user notes, and will be skipped.
- **Duplicate titles** — meetings with the same name (e.g. recurring "Monthly Board Meeting") are disambiguated by the date prefix in the filename. If two meetings with the same title occur on the same day, the second will overwrite the first. This is a known edge case with recurring meetings that hasn't been addressed yet.
