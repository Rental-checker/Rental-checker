# Setting up automated rental checks (no computer required)

This runs `check_all_rentals.py` automatically every 4 hours on GitHub's
free infrastructure, and publishes the report to a URL you can bookmark
and open from your phone - no app, no login, nothing needs to stay on.

## One-time setup (about 10 minutes)

### 1. Create a GitHub account (if you don't have one)
Go to https://github.com and sign up - it's free.

### 2. Create a new repository
- Click the "+" in the top right → "New repository"
- Name it something like `rental-checker`
- Set it to **Public** (this keeps things simple - GitHub Pages hosting
  is free and easy for public repos; a private repo would need a paid
  GitHub plan for Pages, or a different way to view the report - let me
  know if you'd rather go that route)
- Don't add a README/gitignore/license - leave it empty
- Click "Create repository"

### 3. Upload these files
On the new repo's page, click "uploading an existing file" and drag in
everything from this folder, **keeping the folder structure**:
```
check_all_rentals.py
.github/workflows/check_rentals.yml
```
(The `docs/` folder and `seen_all_rentals.json` will be created
automatically by the first run - you don't need to upload those.)

GitHub's web upload should preserve the `.github/workflows/` folder
path automatically if you drag the whole folder in. If it flattens it,
create the folders manually via "Add file" → "Create new file" and
type the full path (e.g. `.github/workflows/check_rentals.yml`) as the
filename - GitHub creates the folders for you.

### 4. Turn on GitHub Pages
- Go to the repo's **Settings** tab → **Pages** (left sidebar)
- Under "Build and deployment" → "Source", choose **Deploy from a branch**
- Under "Branch", choose **main** and folder **/docs**, then Save
- (The `/docs` folder won't exist yet until the first run completes -
  that's fine, just set this now so it's ready)

### 5. Run it once manually to test
- Go to the **Actions** tab → click "Check rentals" on the left
- Click "Run workflow" → "Run workflow" (green button)
- Wait a few minutes (it installs a browser and checks 8 sites, so
  expect ~10-15 minutes for the first run)
- Refresh the page - you should see a green checkmark when it's done

### 6. Find your report URL
- Go back to Settings → Pages - it'll show "Your site is live at
  `https://yourusername.github.io/rental-checker/`"
- Open that URL, bookmark it on your phone - that's your live report,
  updated automatically every 4 hours from now on

## Adjusting things later

- **Change how often it checks**: edit the `cron` line in
  `.github/workflows/check_rentals.yml`. Right now it's
  `0 */4 * * *` (every 4 hours). Cron times are in UTC.
- **Change filters**: edit `MAX_PRICE` or the city lists near the top
  of `check_all_rentals.py`, same as running it locally.
- Any edit you push to the repo takes effect on the next scheduled run
  (or trigger one manually via the Actions tab to test immediately).

## Notes

- Because the repo is public, the report and listing data are visible
  to anyone with the link - not sensitive info, but worth knowing.
- GitHub Actions is free for public repos with no meaningful limits for
  this use case (a few runs a day).
- If a site changes its layout again and a scraper breaks, the workflow
  will still run and publish results from the other working sites -
  it won't fail silently, the Actions tab will show which step failed.
