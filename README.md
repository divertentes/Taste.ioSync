# Taste.ioSync

Import your [Letterboxd](https://letterboxd.com) ratings into [Taste.io](https://taste.io) from a CSV export.

Taste.io has no public API. This script uses the same internal endpoints the website uses.

## What it does

1. Reads `ratings.csv` and `diary.csv` from your Letterboxd data export
2. Skips movies you already rated on Taste.io (unless you pass `--update`)
3. Searches each film on Taste.io and submits your rating

### Rating conversion

Letterboxd and Taste.io share different rating grades, here's the conversion:

| Letterboxd | Taste.io |
|------------|----------|
| 0.5 – 3 ★ | awful |
| 3.5 – 5 ★ | meh |
| 5.5 – 7 ★ | good |
| 7.5 – 10 ★ | amazing |

## Setup

```bash
git clone https://github.com/divertentes/Taste.ioSync.git
cd Taste.ioSync
pip install -r requirements.txt

(Python required → https://www.python.org/downloads/)
```

## Letterboxd export

1. Go to [letterboxd.com/settings/data](https://letterboxd.com/settings/data/)
2. Click **Export Your Data** and download the ZIP
3. Extract it
   
## Taste.io login (token)

1. Log in to [taste.io](https://taste.io)
2. Open DevTools (F12) → **Network** tab → refresh the page
3. Click the `me` request (you can look it up on the filter search bar) → look for the **Request Headers** tab → copy the `Authorization` value **without** `Bearer`
4. Pass it when running:

```bash
python import_letterboxd_to_taste.py --export-dir "%USERPROFILE%\LetterboxdExportPathHere" --token "YOUR_TOKEN"
```

Tokens expire after a while. If you get auth errors, grab a fresh one

## Usage

**Preview** (no token needed):

```bash
python import_letterboxd_to_taste.py --export-dir "%USERPROFILE%\LetterboxdExportPathHere" --dry-run
```

**Import to Taste.io**:

```bash
python import_letterboxd_to_taste.py --export-dir "%USERPROFILE%\LetterboxdExportPathHere" --token "YOUR_TOKEN"
```

### Useful flags

| Flag | Description |
|------|-------------|
| `--token` | Taste.io bearer token |
| `--update` | Overwrite ratings already on Taste.io |
| `--verbose` | Show which search query matched each film |
| `--dry-run` | Print conversions without calling Taste.io |

## Notes

- Only rated films are imported (Taste.io requires a rating)
- Some films may not be found if Taste.io doesn't have them
- A film is only imported if **both title and year** match on Taste.io.

## Disclaimer

Unofficial tool, not affiliated with Letterboxd or Taste.io. Use at your own risk.
