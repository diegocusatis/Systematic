# Systematic stress-test checklist

Start with **Systematic-Setup.exe**. The first transfer works on a copy of your
old reviews. Keep the original folder available while comparing results.

## First installation and migration

1. Close any running Systematic server. Run setup and confirm it fills in your
   original project's `.review-data` folder by itself, and that the details box
   lists the expected reviews, template columns, accepted evidence and history
   counts. If several copies exist, confirm the most recently used one was
   chosen and the others are listed. If Python is not found automatically,
   use **Choose** to select a supported `python.exe`.
2. Add any original templates and exported workbooks kept outside the review
   folder, then click **Install / update**.
3. Confirm setup completes and a **Start Systematic** shortcut appears. Launch it.
   Check that the setup window, launcher and executable icons use Systematic's
   browser green. Setup should show a numeric percentage, a filling progress bar and the current stage,
   with 100% appearing only after successful validation and activation.
   The percentage can pause during downloads or large review-copy operations.
4. Check the review list, then open several PDFs, including large papers and
   any with accepted evidence. Confirm the original page and evidence match.
5. Check approved values, reviewer decisions, outcome rows, template columns,
   worksheet names, source locations, and decision history.
6. Download **Data**, **Verification**, and **Combined** workbooks. Compare them
   with exports from the old app: values, tab order, headers, formatting, cell
   notes, and verification locations should agree. Compare workbook contents,
   rather than file hashes, because generated workbook timestamps can differ.

## Daily use and load

7. Make a small, identifiable test decision. Use **Stop & close**, reopen the
   launcher, and confirm the decision persists.
8. Upload and scan your usual large PDF batch. Switch papers, open evidence,
   and export while processing. Record responsiveness and any error messages.
   Search speed and ranking behavior are the original application's behavior;
   this installer does not change them.
9. Start a scan and click **Stop & close**. The launcher should wait for active
   work to finish, then close. Reopen and verify saved decisions.
10. Open the launcher a second time while it is already running. It should report
    that Systematic is running, without starting a second writer.

## Updates and failure handling

11. Close Systematic and run setup again, leaving the previous-folder field
    blank. Verify the same reviews and your test decision remain, and a new
    backup exists.
    After one successful installation with this installer, repeat with internet
    disconnected and the same Python and optional-feature choices. Matching
    cached packages should be verified and reused. A changed dependency set
    needs its own first download. Review/PDF/export checks still run every time.
12. Try setup while Systematic is open. It should stop and ask you to close the
    application.
13. For a missing-PDF test, use a disposable review dataset with a genuinely
    missing referenced PDF. Import should stop with an identifiable error.
    Simply removing a PDF from a copied folder may not trigger this: the saved
    record can still point to the original PDF, which setup can legitimately copy.
14. If testing two divergent copies of the same review, keep both as disposable
    copies. Import should stop with a conflict message; it must not choose an
    approved value from one copy silently.

## Where to find evidence

Press **Windows+R** and paste:

| Purpose | Location |
| --- | --- |
| Application/setup logs | `%LOCALAPPDATA%\Systematic\logs` |
| Saved reviews and PDFs | `%LOCALAPPDATA%\ReviewExtractor` |
| Backups and transfer reports | `%LOCALAPPDATA%\Systematic\backups` |
| Active version details | `%LOCALAPPDATA%\Systematic\current.json` |

For a failed test, retain the relevant log and note the action, expected result,
actual result, review/paper name, and time. Logs may contain local file paths or
paper names; review them before sharing outside your lab.

Read **Windows-Guide.md** before manually restoring a backup. The delivered
installer is unsigned; local Windows or lab IT policy may require a signed
build for distribution.
