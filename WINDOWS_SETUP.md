# Systematic for Windows

## For lab colleagues

1. You do not need to install anything first. Setup finds a supported 64-bit
   Python 3.12 or 3.11, even when it is not on PATH, or reuses an environment in
   a previous project or installed Systematic version. A custom installation can
   be selected with **Choose**. If none is found, setup installs Python 3.12.10
   from python.org for your Windows user and installs Tesseract OCR for scanned
   PDFs (see below).
2. Download the repository ZIP and extract it, or download the standalone
   **Systematic-Setup.exe** from a Windows release.
3. Double-click **Systematic-Setup.exe**. Close any running copy of Systematic
   before installing or updating.
4. Setup looks for your previous Systematic automatically when it opens. It
   checks the folder setup was started from, Documents (including
   `Documents\GitHub`), Desktop, Downloads, OneDrive, and `source\repos`, entering
   only folders named like Systematic, `review-extractor`, `.review-data` or
   `review-data`. The most recently used copy is filled in, and the window lists
   what will be carried over: reviews, PDFs, extraction-template columns and
   their search terms, accepted and rejected evidence, reviewed and approved
   values, decision history, and reviewer learning examples. Other copies found
   are listed in the details box but never merged silently. Use **Browse** to
   choose a different copy, or clear the box for a brand-new installation.
   For an update of an installed version the box stays blank; your installed
   reviews are kept and backed up. Copies on other drives or computers are not
   searched; select them with **Browse**.
5. Optionally use **Add original templates / exported workbooks** to retain
   original Excel files stored outside the old review folder.
6. Click **Install / update**. The first setup downloads Python packages, and
   Python and Tesseract when they are missing. A selected `python.exe` that is
   unsupported stops setup before it changes the installation.
   A completion percentage, a green progress bar and the current stage are shown.
   Percentages represent completed setup stages, not elapsed time; a large
   download or review collection can stay at one percentage while being processed.
   Setup reaches 100% only after validation and activation have succeeded.
7. Click **Start Systematic**, or use the new desktop shortcut of that name.
   The launcher checks its Python environment and opens a browser when the app
   is ready. Keep the launcher open while working. **Stop & close** lets active
   processing finish and shuts down the app.

### Automatic dependency installation

Everything is declared in `windows/release.json` and verified before it runs.
Setup installs for the current Windows user where possible (no PATH, launcher
or file-association changes) and asks for administrator permission only for a
component that must be installed machine-wide:

| Dependency | When | Installed to | Admin | Verification |
| --- | --- | --- | --- | --- |
| Python 3.12.10 (python.org) | No supported Python found | `%LOCALAPPDATA%\Systematic\python` | Never | python.org checksum and Python Software Foundation signature |
| Python packages | Always | Version environment | Never | Pinned in `requirements.txt` and `requirements-extras.txt`; binary wheels only, SHA-256 cached |
| Tesseract OCR 5.4.0 (UB Mannheim) | No working Tesseract found | `%LOCALAPPDATA%\Systematic\tesseract`, or Program Files as a fallback | Only if the per-user install is refused | Pinned SHA-256; the engine must start and list English |
| Semantic model `all-MiniLM-L6-v2` (~90 MB) | Semantic search selected | `%LOCALAPPDATA%\Systematic\models` | Never | Loaded offline and must produce an embedding |
| Microsoft Visual C++ runtime | Only if PyTorch cannot load its DLLs | Windows | Yes | Valid Microsoft Corporation signature |

An existing Tesseract (Program Files, Local AppData or PATH) is reused. When an
administrator prompt is needed, the setup window says so; if no prompt appears,
check the taskbar. Declining a prompt, or being offline, never stops setup: OCR
stays off (scanned pages remain flagged for manual review) or search uses fast
retrieval, and rerunning setup tries again. The end of the setup log lists each
dependency as `[OK]` or `[MISSING]`.
The launcher turns OCR on automatically for the verified engine; set
`SYSTEMATIC_OCR=0` to opt out. Supported Python versions are deliberately limited
to those exercised by the project CI. If Python is removed later, rerun setup.

The setup window becomes a **Start Systematic** button after a successful
installation. A separate launcher and desktop shortcut take over everyday use.
The downloaded setup file is not self-deleted; you can remove it after confirming
your reviews. Rerunning setup is supported and creates a separate app environment.

### What is preserved

- Every review and its stable paper, item, and outcome identifiers.
- PDFs, including removed-paper records and files referenced outside the original
  review directory when those files are available.
- Imported extraction definitions, worksheet assignments and stored formatting,
  search settings, approved values, accepted/rejected evidence, source locations,
  repeatable outcomes, reviewer learning records, and decision history.
- Original template and export files already in the data folder, plus files
  explicitly added in setup. Additional files live under `original-workbooks`.
- All other files from an imported data folder are retained under `imported-assets`.

Data, verification, and combined workbooks are regenerated from saved records.
Setup tests that all three can be generated. Regression tests compare worksheet
order, values, styles, comments, and source locations before and after a transfer.
Existing exported `.xlsx` files and original uploaded template workbooks are
separate artifacts: add them in setup if they were saved outside review storage.

If both locations contain the same review, identical records can be retained
once. Different versions of that review stop the import without overwriting
either copy. This is not a reviewer-reconciliation feature.

### Where files live

| Location | Contents |
| --- | --- |
| `%LOCALAPPDATA%\ReviewExtractor` | Permanent review database, PDFs, templates and other review assets |
| `%LOCALAPPDATA%\Systematic\Systematic.exe` | Everyday launcher |
| `%LOCALAPPDATA%\Systematic\versions` | Separate application environments for installed versions |
| `%LOCALAPPDATA%\Systematic\current.json` | Active application version and data location |
| `%LOCALAPPDATA%\Systematic\backups` | Previous data and installation information for each successful update |
| `%LOCALAPPDATA%\Systematic\transactions` | Prepared copies/source snapshots, including interrupted or failed setups |
| `%LOCALAPPDATA%\Systematic\logs` | Setup and application logs |
| `%LOCALAPPDATA%\Systematic\package-cache` | Verified reusable package downloads, separate from all review data |

**Delete older Systematic versions** (a setup checkbox, off by default) runs only
after setup has succeeded. It removes superseded folders in `versions`, leftover
`transactions`, and incomplete package downloads. It never touches review data,
`backups`, or a previous project folder. Leave it off if you may need to return to
the previous version, because a rollback needs that version's `versions` folder.

These are per-Windows-user locations. Another lab colleague on the same computer
has a separate installation and review store. The app remains a local,
single-user service. It selects an available loopback port automatically.

Setup does not automatically delete old installations, snapshots or backups.
Their disk usage can be substantial for large PDF collections. Once the updated
reviews and your independent backup have been checked, old version/transaction
folders may be removed while Systematic is closed. Keep the active version
listed in `current.json`.

## Updating

Run the new version's **Systematic-Setup.exe**, leaving the previous-folder box
blank when your reviews already live in Local AppData. Setup stages a full copy,
installs a separate environment, checks database integrity and every recorded
PDF, validates saved records and Excel exports, then activates the new version.
The previous data and environment remain available.
The installed `Systematic.exe` is replaced with the new launcher, and setup
rewrites the desktop shortcut. Each version supplies its own icon file so
Windows does not keep showing the previous version's cached icon. The launcher
window also reads its embedded icon directly. Older downloaded setup files
elsewhere are not removed; use the new setup file for later installations.

Updates reuse verified package downloads when dependencies, optional features,
and the Python runtime match. The new app is still installed in a separate
environment. Cache keys automatically change with the dependency lockfile,
package requirements or interpreter; changed or incomplete cache files are
downloaded again. Updates with matching cached dependencies can install offline.
First setup still needs downloads. Package installation avoids redundant app
builds and defers bytecode compilation until modules are first used. Full data
copies, PDF hashes, saved-record comparisons and export checks remain enabled.

Do not use a second, manually launched copy during installation. Setup checks
for an open legacy database and also checks that the source/destination did not
change while packages were installed. A changed source stops activation.

The optional semantic-search checkbox installs the semantic extra and downloads
its embedding model. The resolved model commit is recorded and reused by
updates, so cached embeddings stay valid; the app only reads the model offline.
Updates retain the checkbox choice. A working model does not establish that a
retrieval-quality target has been met. The optional neural reranker and
PaddleOCR remain manual.

The desktop launcher prepares the offline search models before opening the app.
This avoids a Windows native-library deadlock between first-use semantic search
and the launcher's Stop-command listener. Startup may take several seconds;
scans then reuse the loaded model. During a scan, the interface names the current
extraction field, and queued papers start at 0% instead of showing an old result's
completion percentage.

The opt-in `tests/test_windows_semantic_scan.py` regression exercises a real
background semantic scan while the desktop Stop pipe remains open, followed by
a graceful shutdown. Set `RUN_SEMANTIC_TESTS=1` and
`SYSTEMATIC_TEST_MODEL_CACHE` to an existing Hugging Face model cache to run it.
`SYSTEMATIC_TEST_PYTHON` can select an installed Systematic interpreter; otherwise
the test uses the invoking Python. This test uses synthetic data and no downloads.

## Recovery

A failed package download or failed validation does not activate the new
version. Read the setup log, fix the reported problem, and rerun setup.
If activation is interrupted, setup or the desktop server uses `pending.json`
to restore the previous data, or recognize an already committed installation.
Keep that journal until recovery has finished.

For manual rollback of a completed update:

1. Close all Systematic windows and Python servers.
2. Preserve an independent copy of the entire current
   `%LOCALAPPDATA%\ReviewExtractor` folder, especially any work completed since
   the update.
3. Find the relevant folder in `%LOCALAPPDATA%\Systematic\backups`. Confirm its
   `previous-installation.json` describes the version you want.
4. Restore its entire `data` folder to `%LOCALAPPDATA%\ReviewExtractor`, and copy
   `previous-installation.json` over `%LOCALAPPDATA%\Systematic\current.json`.
   Use a complete restore rather than combining SQLite files from two versions.
5. Start Systematic. The previous environment must still exist under `versions`.

A first installation without previous Local AppData reviews has no previous
data folder to restore; its selected source folder remains unchanged. SQLite
paths in backups refer to the permanent data location, so restore the complete
folder there before using it.

## Maintaining future versions

The research app remains unchanged. Windows-specific behavior is in `windows/`:

- `Desktop.cs`: native setup/launcher UI, Python discovery, process lifecycle.
- `installer.py`: snapshots, PDF relocation, conflict handling and activation.
- `runner.py`: existing FastAPI app with a maintenance lock and graceful stop.
- `validate.py`: staged app initialization and workbook export validation.
- `release.json`: supported Python minor versions, architecture and data format.

The build reads the browser palette from `review_extractor/static/style.css`
for both native windows and the executable icons, so future color changes stay
in sync. Missing palette values fail the build instead of silently using stale colors.

The app version comes from `pyproject.toml`; do not duplicate it in installer
code. A content fingerprint also identifies the exact bundled source.

On Windows, with development requirements installed:

```powershell
python -m pytest -m "not browser"
python scripts/build_windows.py --output dist/windows
python scripts/smoke_windows.py --setup dist/windows/Systematic-Setup.exe --work .windows-smoke/new-release
Copy-Item dist/windows/Systematic-Setup.exe Systematic-Setup.exe
```

Use a new empty smoke-test directory each time. Smoke testing downloads packages
or uses pip's cache, installs twice into that directory (the update uses the
verified package cache with package-index access disabled), checks copied synthetic
reviews and exports, starts the installed server, fetches a PDF, and stops it.
It does not modify real AppData or desktop shortcuts.

Commit the updated root `Systematic-Setup.exe` with the corresponding source
when distributing through GitHub's **Download ZIP**. **Rebuild it after source
changes:** it is a bundled release and does not fetch newer source on its own.
The `Windows distribution` GitHub Actions workflow also builds the current
source, tests it, and uploads a Windows artifact containing setup, launcher,
checksums and the release manifest. It does not publish a release automatically.

To add another Python version, update `windows/release.json`
and the CI Python matrix, then run the full
tests. New database schemas require an explicit, tested migration on the staged
copy and an appropriate `data_format` change. The current installer refuses
unknown tables, conflicting reviews, changed saved records, and unsupported
downgrades instead of guessing how to convert them.

The current executables are unsigned. Organizational Windows policies may
require code signing before lab-wide distribution. The build uses the Windows
.NET Framework compiler and an explicit source-file allowlist; it never bundles
local databases, user PDFs, virtual environments, or private test data.
