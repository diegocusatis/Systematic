// Native Windows bootstrap: works before Python has been installed, and installs
// the pinned python.org release for this user when none is found.
// Build with the Windows .NET Framework compiler; no runtime download or admin rights.
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.IO.Compression;
using System.Net;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Security.Cryptography.X509Certificates;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using System.Web.Script.Serialization;
using System.Windows.Forms;
using Microsoft.Win32;

internal static class Desktop
{
    internal static readonly string Home = Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "Systematic");
    internal static readonly string Data = Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "ReviewExtractor");
    internal static readonly JavaScriptSerializer Json = new JavaScriptSerializer();
    // Shared browser palette, embedded by the build from style.css.
    internal static Color Brand(string name)
    {
        return ColorTranslator.FromHtml((string)((Dictionary<string, object>)Manifest()["colors"])[name]);
    }

    [STAThread]
    private static int Main(string[] args)
    {
        Application.EnableVisualStyles();
        Application.SetCompatibleTextRenderingDefault(false);
        try
        {
            // Reproducible, unattended checks used by the release pipeline.
            if (args.Length == 3 && args[0] == "--probe-python")
            {
                string found = Probe(args[1], Strings(Manifest()["supported_python"]));
                File.WriteAllText(args[2], found ?? "UNSUPPORTED");
                return found == null ? 2 : 0;
            }
            if (args.Length == 3 && args[0] == "--detect-python")
            {
                string found = FindPython("", Strings(Manifest()["supported_python"]), args[1]);
                File.WriteAllText(args[2], found ?? "UNSUPPORTED");
                return found == null ? 2 : 0;
            }
            if (args.Length == 2 && args[0] == "--extract")
            {
                string destination = Path.GetFullPath(args[1]);
                if (Directory.Exists(destination) && Directory.GetFileSystemEntries(destination).Length != 0)
                    return 3;
                Directory.CreateDirectory(destination);
                MainWindow.ExtractPayload(destination);
                return 0;
            }
            using (MainWindow window = new MainWindow())
            {
                if (args.Length == 2 && args[0] == "--preview")
                    window.PreviewPath = Path.GetFullPath(args[1]);
                Application.Run(window);
            }
            return 0;
        }
        catch (Exception ex)
        {
            MessageBox.Show(ex.Message, "Systematic", MessageBoxButtons.OK, MessageBoxIcon.Error);
            return 1;
        }
    }

    internal static Dictionary<string, object> Manifest()
    {
        using (Stream stream = Assembly.GetExecutingAssembly().GetManifestResourceStream("release.json"))
        using (StreamReader reader = new StreamReader(stream))
            return Json.Deserialize<Dictionary<string, object>>(reader.ReadToEnd());
    }

    internal static string[] Strings(object value)
    {
        List<string> result = new List<string>();
        foreach (object item in (System.Collections.IEnumerable)value)
            result.Add(Convert.ToString(item));
        return result.ToArray();
    }

    internal static string Quote(string value)
    {
        // CommandLineToArgvW-compatible quoting, including trailing backslashes.
        StringBuilder text = new StringBuilder("\"");
        int slashes = 0;
        foreach (char c in value)
        {
            if (c == '\\') { slashes++; continue; }
            if (c == '"') { text.Append('\\', slashes * 2 + 1); text.Append(c); slashes = 0; continue; }
            text.Append('\\', slashes); slashes = 0; text.Append(c);
        }
        text.Append('\\', slashes * 2); text.Append('"');
        return text.ToString();
    }

    internal static ProcessStartInfo StartInfo(string executable, string arguments)
    {
        ProcessStartInfo start = new ProcessStartInfo(executable, arguments);
        start.UseShellExecute = false;
        start.CreateNoWindow = true;
        start.RedirectStandardOutput = true;
        start.RedirectStandardError = true;
        start.RedirectStandardInput = true;
        start.StandardOutputEncoding = Encoding.UTF8;
        start.StandardErrorEncoding = Encoding.UTF8;
        start.EnvironmentVariables["PYTHONUTF8"] = "1";
        start.EnvironmentVariables["PYTHONNOUSERSITE"] = "1";
        start.EnvironmentVariables.Remove("PYTHONPATH");
        start.EnvironmentVariables.Remove("PYTHONHOME");
        return start;
    }

    internal static string Probe(string executable, string[] supported)
    {
        try
        {
            // A venv reports its base Python, so setup never depends on an older version folder.
            string code = "import sys,struct,venv,ensurepip; print(str(sys.version_info.major)+'.'+str(sys.version_info.minor)+'|'+str(struct.calcsize('P')*8)+'|'+getattr(sys,'_base_executable',sys.executable))";
            using (Process process = Process.Start(StartInfo(executable, "-I -c " + Quote(code))))
            {
                Task<string> output = process.StandardOutput.ReadToEndAsync();
                Task<string> errors = process.StandardError.ReadToEndAsync();
                if (!process.WaitForExit(8000)) { process.Kill(); return null; }
                string[] fields = output.Result.Trim().Split('|');
                if (process.ExitCode == 0 && fields.Length == 3 && fields[1] == "64"
                    && Array.IndexOf(supported, fields[0]) >= 0)
                    return fields[2];
            }
        }
        catch { }
        return null;
    }

    internal static string FindPython(string chosen, string[] supported, string previousFolder = null)
    {
        if (!String.IsNullOrWhiteSpace(chosen))
            return Probe(chosen, supported);
        List<string> candidates = new List<string>();
        // Standard per-user/per-machine CPython registrations, both registry views.
        foreach (RegistryHive hive in new RegistryHive[] { RegistryHive.CurrentUser, RegistryHive.LocalMachine })
        foreach (RegistryView view in new RegistryView[] { RegistryView.Registry64, RegistryView.Registry32 })
        {
            try
            {
                using (RegistryKey root = RegistryKey.OpenBaseKey(hive, view))
                using (RegistryKey python = root.OpenSubKey(@"Software\Python"))
                {
                    if (python == null) continue;
                    foreach (string company in python.GetSubKeyNames())
                    using (RegistryKey vendor = python.OpenSubKey(company))
                    foreach (string tag in vendor.GetSubKeyNames())
                    using (RegistryKey install = vendor.OpenSubKey(tag + @"\InstallPath"))
                    {
                        if (install == null) continue;
                        string executable = install.GetValue("ExecutablePath") as string;
                        string folder = install.GetValue("") as string;
                        if (executable != null) candidates.Add(executable);
                        if (folder != null) candidates.Add(Path.Combine(folder, "python.exe"));
                    }
                }
            }
            catch { }
        }
        // Python Launcher lists real paths without triggering a Python download.
        try
        {
            using (Process process = Process.Start(StartInfo("py.exe", "-0p")))
            {
                Task<string> output = process.StandardOutput.ReadToEndAsync();
                Task<string> errors = process.StandardError.ReadToEndAsync();
                if (process.WaitForExit(5000))
                {
                    foreach (string line in output.Result.Split('\n'))
                    {
                        int drive = line.IndexOf(":\\", StringComparison.Ordinal);
                        if (drive > 0) candidates.Add(line.Substring(drive - 1).Trim().Trim('"'));
                    }
                }
                else process.Kill();
            }
        }
        catch { }
        foreach (string entry in (Environment.GetEnvironmentVariable("PATH") ?? "").Split(';'))
        {
            if (entry.IndexOf("WindowsApps", StringComparison.OrdinalIgnoreCase) >= 0) continue;
            if (!String.IsNullOrWhiteSpace(entry)) candidates.Add(Path.Combine(entry.Trim('"'), "python.exe"));
        }
        foreach (string minor in supported)
        {
            string folder = "Python" + minor.Replace(".", "");
            candidates.Add(Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                                        "Programs", "Python", folder, "python.exe"));
        }
        // Manual installations often already have a working environment in the
        // selected project, even if Python's launcher/registry entry is absent.
        if (!String.IsNullOrWhiteSpace(previousFolder))
        {
            string prior = Path.GetFullPath(previousFolder);
            candidates.Add(Path.Combine(prior, ".venv", "Scripts", "python.exe"));
            string parent = Path.GetDirectoryName(prior);
            if (parent != null) candidates.Add(Path.Combine(parent, ".venv", "Scripts", "python.exe"));
        }
        try
        {
            string stateFile = Path.Combine(Home, "current.json");
            if (File.Exists(stateFile))
            {
                Dictionary<string, object> state = Json.Deserialize<Dictionary<string, object>>(File.ReadAllText(stateFile));
                candidates.Add((string)state["python"]);
            }
        }
        catch { }
        // Python that a previous setup installed for this user.
        candidates.Add(Path.Combine(Home, "python", "python.exe"));
        HashSet<string> seen = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        foreach (string candidate in candidates)
        {
            if (!File.Exists(candidate) || !seen.Add(candidate)) continue;
            string found = Probe(candidate, supported);
            if (found != null) return found;
        }
        return null;
    }

    // Installs the pinned python.org release for this Windows user only: no admin
    // rights, PATH, launcher or file-association changes. The download must match
    // python.org's published checksum and carry a valid Python Software Foundation
    // signature before it runs.
    internal static string InstallPython(string[] supported)
    {
        Dictionary<string, object> spec = (Dictionary<string, object>)Manifest()["python_download"];
        string url = (string)spec["url"];
        string downloads = Path.Combine(Home, "downloads");
        Directory.CreateDirectory(downloads);
        string installer = Path.Combine(downloads, Path.GetFileName(new Uri(url).LocalPath));
        string partial = installer + ".partial";
        ServicePointManager.SecurityProtocol |= SecurityProtocolType.Tls12;
        try
        {
            using (WebClient client = new WebClient()) client.DownloadFile(url, partial);
            string md5;
            using (MD5 hash = MD5.Create())
            using (FileStream stream = File.OpenRead(partial))
                md5 = BitConverter.ToString(hash.ComputeHash(stream)).Replace("-", "").ToLowerInvariant();
            if (md5 != ((string)spec["md5"]).ToLowerInvariant() || !SignedBy(partial, (string)spec["signer"]))
                throw new InvalidOperationException("The downloaded Python installer failed its integrity check and was not run.");
            if (File.Exists(installer)) File.Delete(installer);
            File.Move(partial, installer);
            string target = Path.Combine(Home, "python");
            string arguments = "/quiet InstallAllUsers=0 PrependPath=0 AssociateFiles=0 Shortcuts=0 Include_launcher=0"
                + " InstallLauncherAllUsers=0 Include_doc=0 Include_test=0 Include_tcltk=0 Include_pip=1"
                + " TargetDir=" + Quote(target);
            using (Process process = Process.Start(new ProcessStartInfo(installer, arguments) { UseShellExecute = false, CreateNoWindow = true }))
            {
                if (!process.WaitForExit(15 * 60 * 1000))
                {
                    try { process.Kill(); } catch { }
                    throw new InvalidOperationException("The Python installer did not finish within 15 minutes.");
                }
                // 3010: installed, Windows restart recommended.
                if (process.ExitCode != 0 && process.ExitCode != 3010)
                    throw new InvalidOperationException("The Python installer stopped with code " + process.ExitCode + ".");
            }
            // An existing registration of the same version may install elsewhere; search again.
            return Probe(Path.Combine(target, "python.exe"), supported) ?? FindPython("", supported);
        }
        finally
        {
            foreach (string leftover in new string[] { partial, installer })
                try { if (File.Exists(leftover)) File.Delete(leftover); } catch { }
        }
    }

    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    private struct WinTrustFileInfo
    {
        public uint cbStruct;
        [MarshalAs(UnmanagedType.LPWStr)] public string pcwszFilePath;
        public IntPtr hFile;
        public IntPtr pgKnownSubject;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct WinTrustData
    {
        public uint cbStruct;
        public IntPtr pPolicyCallbackData;
        public IntPtr pSIPClientData;
        public uint dwUIChoice;
        public uint fdwRevocationChecks;
        public uint dwUnionChoice;
        public IntPtr pFile;
        public uint dwStateAction;
        public IntPtr hWVTStateData;
        public IntPtr pwszURLReference;
        public uint dwProvFlags;
        public uint dwUIContext;
        public IntPtr pSignatureSettings;
    }

    [DllImport("wintrust.dll", ExactSpelling = true, SetLastError = false)]
    private static extern int WinVerifyTrust(IntPtr window, [MarshalAs(UnmanagedType.LPStruct)] Guid action, ref WinTrustData data);

    // Windows' own Authenticode check (untampered file, trusted chain), then the signer's name.
    internal static bool SignedBy(string path, string signer)
    {
        WinTrustFileInfo file = new WinTrustFileInfo { cbStruct = (uint)Marshal.SizeOf(typeof(WinTrustFileInfo)), pcwszFilePath = path };
        IntPtr pointer = Marshal.AllocHGlobal(Marshal.SizeOf(typeof(WinTrustFileInfo)));
        try
        {
            Marshal.StructureToPtr(file, pointer, false);
            WinTrustData data = new WinTrustData
            {
                cbStruct = (uint)Marshal.SizeOf(typeof(WinTrustData)),
                dwUIChoice = 2,      // WTD_UI_NONE
                dwUnionChoice = 1,   // WTD_CHOICE_FILE
                pFile = pointer
            };
            Guid verifyV2 = new Guid("00AAC56B-CD44-11d0-8CC2-00C04FC295EE");
            if (WinVerifyTrust(IntPtr.Zero, verifyV2, ref data) != 0) return false;
        }
        finally
        {
            Marshal.DestroyStructure(pointer, typeof(WinTrustFileInfo));
            Marshal.FreeHGlobal(pointer);
        }
        try
        {
            X509Certificate2 certificate = new X509Certificate2(X509Certificate.CreateFromSignedFile(path));
            return String.Equals(certificate.GetNameInfo(X509NameType.SimpleName, false), signer, StringComparison.Ordinal);
        }
        catch (CryptographicException) { return false; }
    }

    internal static void Open(string target)
    {
        Process.Start(new ProcessStartInfo(target) { UseShellExecute = true });
    }

    internal static void Shortcut()
    {
        Type type = Type.GetTypeFromProgID("WScript.Shell");
        dynamic shell = Activator.CreateInstance(type);
        string path = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.DesktopDirectory), "Start Systematic.lnk");
        dynamic link = shell.CreateShortcut(path);
        link.TargetPath = Path.Combine(Home, "Systematic.exe");
        link.WorkingDirectory = Home;
        link.Description = "Open Systematic and your saved reviews";
        Dictionary<string, object> state = Json.Deserialize<Dictionary<string, object>>(
            File.ReadAllText(Path.Combine(Home, "current.json")));
        // A version-specific icon path prevents Explorer from retaining an old
        // cached icon even though the permanent launcher path stays the same.
        link.IconLocation = (state.ContainsKey("launcher_icon") ?
            (string)state["launcher_icon"] : Path.Combine(Home, "Systematic.exe")) + ",0";
        link.Save();
        System.Runtime.InteropServices.Marshal.FinalReleaseComObject(link);
        System.Runtime.InteropServices.Marshal.FinalReleaseComObject(shell);
    }
}

internal sealed class MainWindow : Form
{
#if LAUNCHER
    private static readonly bool IsSetup = false;
#else
    private static readonly bool IsSetup = true;
#endif
    internal string PreviewPath;
    private readonly Label status = new Label();
    private readonly TextBox log = new TextBox();
    private readonly TextBox source = new TextBox();
    private readonly TextBox python = new TextBox();
    private readonly Button primary = new Button();
    private readonly Button openBrowser = new Button();
    private readonly CheckBox semantic = new CheckBox();
    private readonly CheckBox removeOld = new CheckBox();
    private readonly Label progress = new Label();
    // Determinate bar beside the percentage; drawn in the brand green, not the system theme.
    private readonly Panel progressTrack = new Panel();
    private readonly Panel progressFill = new Panel();
    private int completionPercent;
    private readonly List<string> files = new List<string>();
    private readonly List<Control> setupControls = new List<Control>();
    private Process child;
    private bool busy;
    private bool finished;
    private bool closeAfterStop;
    private string url;
    private string sessionLog;
    private readonly object logLock = new object();
    private string extracted;

    internal MainWindow()
    {
        Text = IsSetup ? "Systematic • Setup & update" : "Systematic";
        ClientSize = new Size(760, IsSetup ? 680 : 475);
        MinimumSize = new Size(776, IsSetup ? 719 : 514);
        FormBorderStyle = FormBorderStyle.FixedSingle;
        MaximizeBox = false;
        StartPosition = FormStartPosition.CenterScreen;
        BackColor = Desktop.Brand("background");
        Font = new Font("Segoe UI", 10);
        using (Stream icon = Assembly.GetExecutingAssembly().GetManifestResourceStream("systematic.ico"))
            Icon = new Icon(icon);

        Panel banner = new Panel { BackColor = Desktop.Brand("green"), Dock = DockStyle.Top, Height = 112 };
        banner.Controls.Add(new Label {
            Text = "Systematic", ForeColor = Color.White, Font = new Font("Segoe UI Semibold", 26),
            Location = new Point(28, 14), AutoSize = true });
        banner.Controls.Add(new Label {
            Text = IsSetup ? "A simpler start. Your research, carried forward." : "Your local research workspace",
            ForeColor = Desktop.Brand("pale"), Location = new Point(31, 69), AutoSize = true });
        Controls.Add(banner);
        int y = 130;
        if (IsSetup)
        {
            AddLabel("1   Set up or update this computer", y, true); y += 29;
            AddLabel("Saved reviews stay in Local AppData. Previous copies and update backups are retained.", y, false); y += 35;
            AddLabel("Previous Systematic / review folder (found automatically; change with Browse)", y, true); y += 25;
            source.SetBounds(30, y, 592, 29); Controls.Add(source);
            Button browse = ButtonAt("Browse…", 632, y - 1, 98, delegate {
                using (FolderBrowserDialog dialog = new FolderBrowserDialog {
                    Description = "Select the old Systematic folder or the folder containing reviews.sqlite3" })
                    if (dialog.ShowDialog(this) == DialogResult.OK) source.Text = dialog.SelectedPath;
            });
            setupControls.Add(source); setupControls.Add(browse); y += 37;
            Button extra = ButtonAt("Add original templates / exported workbooks…", 30, y, 400, delegate {
                using (OpenFileDialog dialog = new OpenFileDialog {
                    Multiselect = true, Filter = "Excel workbooks|*.xlsx;*.xlsm;*.xls|All files|*.*",
                    Title = "Keep additional original workbooks with your reviews" })
                    if (dialog.ShowDialog(this) == DialogResult.OK) {
                        files.AddRange(dialog.FileNames);
                        status.Text = files.Count + " additional workbook(s) will be preserved.";
                    }
            });
            setupControls.Add(extra); y += 42;
            AddLabel("Python " + String.Join(" or ", Desktop.Strings(Desktop.Manifest()["supported_python"]))
                     + ", 64-bit (detected automatically)", y, true); y += 25;
            python.SetBounds(30, y, 592, 29); Controls.Add(python);
            Button choosePython = ButtonAt("Choose…", 632, y - 1, 98, delegate {
                using (OpenFileDialog dialog = new OpenFileDialog { Filter = "Python executable|python.exe", Title = "Select a supported python.exe" })
                    if (dialog.ShowDialog(this) == DialogResult.OK) python.Text = dialog.FileName;
            });
            setupControls.Add(python); setupControls.Add(choosePython); y += 37;
            semantic.Text = "Include optional local semantic search (larger download)";
            semantic.SetBounds(30, y, 700, 25); Controls.Add(semantic); setupControls.Add(semantic); y += 30;
            removeOld.Text = "After a successful setup, delete older Systematic versions (reviews and backups are kept)";
            removeOld.SetBounds(30, y, 700, 25); Controls.Add(removeOld); setupControls.Add(removeOld); y += 35;
        }
        else
        {
            AddLabel("Your saved reviews are ready when you are.", y, true); y += 33;
            AddLabel("Keep this window open while working. Stop & close saves a clean shutdown.", y, false); y += 37;
        }
        status.Text = IsSetup ? "Ready. Setup checks Python before making installation changes." : "Checking your installation…";
        status.SetBounds(30, y, 700, 45); Controls.Add(status); y += 47;
        progress.SetBounds(30, y, IsSetup ? 190 : 700, 32);
        if (IsSetup)
        {
            progressTrack.SetBounds(230, y + 9, 500, 16);
            progressTrack.BackColor = Color.FromArgb(222, 228, 224);
            progressTrack.AccessibleName = "Installation progress bar";
            progressFill.SetBounds(0, 0, 0, 16);
            progressFill.BackColor = Desktop.Brand("green");
            progressTrack.Controls.Add(progressFill);
            Controls.Add(progressTrack);
        }
        progress.ForeColor = Desktop.Brand("green");
        progress.Font = new Font("Segoe UI Semibold", 16);
        progress.Text = IsSetup ? "0% complete" : "Starting…";
        progress.AccessibleName = "Installation completion";
        Controls.Add(progress); y += 40;
        log.SetBounds(30, y, 700, IsSetup ? 80 : 125);
        log.Multiline = true; log.ReadOnly = true; log.ScrollBars = ScrollBars.Vertical;
        log.BackColor = Color.White; log.BorderStyle = BorderStyle.FixedSingle;
        log.Font = new Font("Consolas", 8.5f); Controls.Add(log);
        int bottom = ClientSize.Height - 51;
        ButtonAt("Open review folder", 30, bottom, 170, delegate {
            if (Directory.Exists(Desktop.Data)) Desktop.Open(Desktop.Data);
            else MessageBox.Show(this, "Your review folder will be created after setup succeeds.", "Systematic");
        });
        openBrowser.Text = "Open browser"; openBrowser.SetBounds(422, bottom, 140, 34);
        openBrowser.Enabled = false; openBrowser.Click += delegate { if (url != null) Desktop.Open(url); };
        Controls.Add(openBrowser);
        primary.Text = IsSetup ? "Install / update" : "Starting…";
        primary.SetBounds(572, bottom, 158, 34); primary.BackColor = Desktop.Brand("green");
        primary.ForeColor = Color.White; primary.FlatStyle = FlatStyle.Flat;
        primary.Click += Primary; Controls.Add(primary);
        Shown += OnShown;
        FormClosing += OnClosing;
    }

    private void AddLabel(string text, int y, bool bold)
    {
        Controls.Add(new Label { Text = text, Location = new Point(30, y), Size = new Size(700, 25),
            ForeColor = Desktop.Brand("ink"),
            Font = new Font("Segoe UI", bold ? 10 : 9, bold ? FontStyle.Bold : FontStyle.Regular) });
    }
    private Button ButtonAt(string text, int x, int y, int width, EventHandler action)
    {
        Button button = new Button { Text = text, Location = new Point(x, y), Size = new Size(width, 32) };
        button.Click += action; Controls.Add(button); return button;
    }
    private void UI(Action action)
    {
        if (IsDisposed || !IsHandleCreated) return;
        if (InvokeRequired) BeginInvoke(action); else action();
    }
    private void Log(string line)
    {
        if (String.IsNullOrWhiteSpace(line)) return;
        lock (logLock)
            if (sessionLog != null) File.AppendAllText(sessionLog, line + Environment.NewLine, Encoding.UTF8);
        if (IsSetup && line.StartsWith("SYSTEMATIC_PROGRESS=", StringComparison.Ordinal))
        {
            try {
                Dictionary<string, object> update = Desktop.Json.Deserialize<Dictionary<string, object>>(
                    line.Substring("SYSTEMATIC_PROGRESS=".Length));
                int percent = Convert.ToInt32(update["percent"]);
                string message = (string)update["message"];
                if (percent >= 0 && percent <= 99)
                    UI(delegate { SetProgress(percent, message); });
                return;
            } catch { /* Keep unexpected output visible for diagnosis. */ }
        }
        UI(delegate { log.AppendText(line + Environment.NewLine); });
        if (line.StartsWith("SYSTEMATIC_READY=", StringComparison.Ordinal))
        {
            url = line.Substring("SYSTEMATIC_READY=".Length);
            UI(delegate {
                status.Text = "Systematic is running. Your browser is ready.";
                primary.Text = "Stop & close"; primary.Enabled = true; openBrowser.Enabled = true;
                progress.Text = "Ready";
                Desktop.Open(url);
            });
        }
    }
    private void SetProgress(int percent, string message)
    {
        if (percent < completionPercent) return;
        completionPercent = percent;
        progress.Text = percent + "% complete";
        progressFill.Width = progressTrack.ClientSize.Width * Math.Min(percent, 100) / 100;
        progress.AccessibleDescription = message;
        status.Text = message;
        log.AppendText(percent + "%  " + message + Environment.NewLine);
    }
    private void OnShown(object sender, EventArgs args)
    {
        if (PreviewPath != null)
        {
            // UI rendering test only; no detection, setup, or server process.
            using (Bitmap bitmap = new Bitmap(Width, Height)) {
                DrawToBitmap(bitmap, new Rectangle(0, 0, Width, Height)); bitmap.Save(PreviewPath);
            }
            BeginInvoke(new Action(Close)); return;
        }
        if (!IsSetup) { StartApplication(); return; }
        DiscoverPrevious();
        try {
            string current = Path.Combine(Desktop.Home, "current.json");
            if (File.Exists(current)) {
                Dictionary<string, object> state = Desktop.Json.Deserialize<Dictionary<string, object>>(File.ReadAllText(current));
                semantic.Checked = state.ContainsKey("semantic") && (bool)state["semantic"];
            }
        } catch { }
    }
    // Bounded search roots. Known-folder paths follow OneDrive redirection of
    // Documents and Desktop; only Systematic-like folders are entered below them.
    private static List<string> SearchRoots()
    {
        List<string> roots = new List<string>();
        string profile = Environment.GetFolderPath(Environment.SpecialFolder.UserProfile);
        string exeFolder = Path.GetDirectoryName(Application.ExecutablePath);
        roots.Add(exeFolder);
        roots.Add(Path.GetDirectoryName(exeFolder));
        roots.Add(Environment.GetFolderPath(Environment.SpecialFolder.MyDocuments));
        roots.Add(Environment.GetFolderPath(Environment.SpecialFolder.DesktopDirectory));
        roots.Add(Path.Combine(profile, "Downloads"));
        roots.Add(Path.Combine(profile, "Documents"));
        roots.Add(Path.Combine(profile, "Desktop"));
        foreach (string variable in new string[] { "OneDrive", "OneDriveConsumer", "OneDriveCommercial" })
        {
            string cloud = Environment.GetEnvironmentVariable(variable);
            if (String.IsNullOrWhiteSpace(cloud)) continue;
            roots.Add(cloud);
            roots.Add(Path.Combine(cloud, "Documents"));
            roots.Add(Path.Combine(cloud, "Desktop"));
        }
        roots.Add(profile);
        List<string> result = new List<string>();
        foreach (string root in roots)
            if (!String.IsNullOrWhiteSpace(root) && Directory.Exists(root) && !result.Contains(root)) result.Add(root);
        return result;
    }

    private static bool SearchedName(string name)
    {
        string lower = name.ToLowerInvariant();
        return lower == ".review-data" || lower == "review-data" || lower == "github" || lower == "source"
            || lower == "repos" || lower == "projects" || lower == "research"
            || lower.StartsWith("systematic") || lower.StartsWith("review-extractor")
            || lower.StartsWith("review_extractor") || lower.StartsWith("review extractor");
    }

    // Used only when Python is not available yet; installer.py --discover is the main search.
    private static void FindData(string root, int depth, List<string> found)
    {
        if (depth > 4 || !Directory.Exists(root)) return;
        try {
            if ((File.GetAttributes(root) & FileAttributes.ReparsePoint) != 0) return;
            string full = Path.GetFullPath(root).TrimEnd('\\');
            if (full.Equals(Desktop.Data, StringComparison.OrdinalIgnoreCase)
                || full.StartsWith(Desktop.Home + "\\", StringComparison.OrdinalIgnoreCase)) return;
            if (File.Exists(Path.Combine(root, "reviews.sqlite3"))) {
                if (!found.Contains(full)) found.Add(full);
                return;
            }
            foreach (string folder in Directory.GetDirectories(root))
                if (SearchedName(Path.GetFileName(folder)))
                    FindData(folder, depth + 1, found);
        } catch { }
    }

    private async void DiscoverPrevious()
    {
        bool hasInstalled = File.Exists(Path.Combine(Desktop.Data, "reviews.sqlite3"));
        List<string> roots = SearchRoots();
        primary.Enabled = false;
        status.Text = "Looking for a previous Systematic on this computer…";
        string chosen = null;
        try
        {
            string[] supported = Desktop.Strings(Desktop.Manifest()["supported_python"]);
            string executable = await Task.Run(() => Desktop.FindPython("", supported, null));
            if (IsDisposed) return;
            Dictionary<string, object> discovery = null;
            if (executable != null)
            {
                if (python.Text.Trim().Length == 0) python.Text = executable;
                discovery = await Task.Run(() => RunDiscovery(executable, roots));
                if (IsDisposed) return;
            }
            chosen = discovery != null ? ApplyDiscovery(discovery, hasInstalled) : ApplyFallback(roots, hasInstalled);
        }
        catch (Exception error)
        {
            if (IsDisposed) return;
            Log("The automatic search for a previous Systematic did not finish: " + error.Message);
        }
        if (IsDisposed) return;
        primary.Enabled = true;
        if (hasInstalled && chosen == null)
            status.Text = "Ready to update. Your installed reviews are kept and backed up.";
        else if (chosen == null && source.Text.Trim().Length == 0)
            status.Text = "No previous Systematic was found. Use Browse if your old copy is elsewhere, or continue for a new installation.";
    }

    private static Dictionary<string, object> RunDiscovery(string executable, List<string> roots)
    {
        string folder = Path.Combine(Path.GetTempPath(), "SystematicSetup-" + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(folder);
        try
        {
            ExtractPayloadFile(folder, "installer.py");
            string rootsFile = Path.Combine(folder, "roots.json");
            File.WriteAllText(rootsFile, Desktop.Json.Serialize(roots), Encoding.UTF8);
            string arguments = "-I -u " + Desktop.Quote(Path.Combine(folder, "installer.py"))
                + " --discover --roots-json " + Desktop.Quote(rootsFile)
                + " --home " + Desktop.Quote(Desktop.Home) + " --data " + Desktop.Quote(Desktop.Data);
            using (Process process = Process.Start(Desktop.StartInfo(executable, arguments)))
            {
                process.StandardInput.Close();
                Task<string> output = process.StandardOutput.ReadToEndAsync();
                Task<string> errors = process.StandardError.ReadToEndAsync();
                if (!process.WaitForExit(120000)) { try { process.Kill(); } catch { } return null; }
                foreach (string line in output.Result.Split('\n'))
                {
                    string text = line.Trim();
                    if (text.StartsWith("SYSTEMATIC_DISCOVERY=", StringComparison.Ordinal))
                        return Desktop.Json.Deserialize<Dictionary<string, object>>(
                            text.Substring("SYSTEMATIC_DISCOVERY=".Length));
                }
            }
            return null;
        }
        finally
        {
            try { Directory.Delete(folder, true); } catch { }
        }
    }

    private static string Field(Dictionary<string, object> values, string key)
    {
        return values.ContainsKey(key) && values[key] != null ? Convert.ToString(values[key]) : "";
    }

    private static bool Flag(Dictionary<string, object> values, string key)
    {
        return values.ContainsKey(key) && values[key] is bool && (bool)values[key];
    }

    // Prefills the newest usable copy that has not been imported yet. Several
    // copies are listed, never merged silently; an update leaves the field blank.
    private string ApplyDiscovery(Dictionary<string, object> discovery, bool hasInstalled)
    {
        List<Dictionary<string, object>> candidates = new List<Dictionary<string, object>>();
        if (discovery.ContainsKey("candidates") && discovery["candidates"] != null)
            foreach (object item in (System.Collections.IEnumerable)discovery["candidates"])
                candidates.Add((Dictionary<string, object>)item);
        Dictionary<string, object> best = null;
        foreach (Dictionary<string, object> candidate in candidates)
            if (Flag(candidate, "usable") && !Flag(candidate, "already_imported")) { best = candidate; break; }
        if (hasInstalled)
        {
            if (best != null)
                Log("A previous Systematic copy that has not been imported was also found: " + Field(best, "path")
                    + " (last used " + Field(best, "last_used_text") + "; " + Field(best, "description")
                    + "). Select it with Browse only if it holds reviews you still need.");
            return null;
        }
        if (best == null)
        {
            if (candidates.Count > 0)
                Log("Previous Systematic folders were found, but none can be imported: "
                    + String.Join("; ", candidates.ConvertAll(c => Field(c, "path"))));
            return null;
        }
        string path = Field(best, "path");
        if (source.Text.Trim().Length == 0) source.Text = path;
        status.Text = "Found your previous Systematic (last used " + Field(best, "last_used_text")
            + "). Its reviews, templates, accepted evidence and verification will be carried over.";
        Log("Previous Systematic: " + path);
        Log("Will carry over: " + Field(best, "description"));
        foreach (Dictionary<string, object> candidate in candidates)
        {
            if (candidate == best) continue;
            Log("Also found, not selected: " + Field(candidate, "path")
                + (Flag(candidate, "usable") ? " (last used " + Field(candidate, "last_used_text") + "; "
                   + Field(candidate, "description") + ")" : " (could not be read)")
                + (Flag(candidate, "already_imported") ? " - already imported" : ""));
        }
        if (candidates.Count > 1)
            Log("The most recently used copy was selected. Use Browse to choose a different one.");
        return path;
    }

    private string ApplyFallback(List<string> roots, bool hasInstalled)
    {
        List<string> found = new List<string>();
        foreach (string root in roots) FindData(root, 0, found);
        if (hasInstalled || found.Count == 0) return null;
        found.Sort((a, b) => File.GetLastWriteTimeUtc(Path.Combine(b, "reviews.sqlite3"))
            .CompareTo(File.GetLastWriteTimeUtc(Path.Combine(a, "reviews.sqlite3"))));
        if (source.Text.Trim().Length == 0) source.Text = found[0];
        status.Text = "Found a previous Systematic. Its reviews will be carried over once Python is available.";
        Log("Previous Systematic: " + found[0]);
        for (int i = 1; i < found.Count; i++) Log("Also found, not selected: " + found[i]);
        return found[0];
    }
    private async void Primary(object sender, EventArgs args)
    {
        if (!IsSetup) { StopApplication(); return; }
        if (finished) { Desktop.Open(Path.Combine(Desktop.Home, "Systematic.exe")); Close(); return; }
        string selectedPython = python.Text.Trim();
        string selectedSource = source.Text.Trim();
        bool useSemantic = semantic.Checked;
        bool deleteOld = removeOld.Checked;
        busy = true; primary.Enabled = false;
        foreach (Control control in setupControls) control.Enabled = false;
        completionPercent = 0;
        SetProgress(0, "Checking for a compatible Python installation…");
        try
        {
            // Supported versions come from the release resource, never a guessed system default.
            Dictionary<string, object> manifest = Desktop.Manifest();
            string[] supported = Desktop.Strings(manifest["supported_python"]);
            string executable = await Task.Run(() => Desktop.FindPython(selectedPython, supported, selectedSource));
            if (executable == null && selectedPython.Length > 0)
                throw new InvalidOperationException("Setup stopped: the selected python.exe is not a supported 64-bit Python "
                    + String.Join(" or ", supported) + ".\n\nClear the Python box to let setup install Python for you, or choose another python.exe.");
            if (executable == null)
            {
                Dictionary<string, object> pinned = (Dictionary<string, object>)manifest["python_download"];
                SetProgress(1, "Python was not found. Downloading and installing Python " + pinned["version"] + " for this Windows user…");
                try { executable = await Task.Run(() => Desktop.InstallPython(supported)); }
                catch (Exception error)
                {
                    throw new InvalidOperationException("Setup could not install Python automatically: " + error.Message
                        + "\n\nCheck your internet connection and retry, or install Python " + String.Join(" or ", supported)
                        + " (64-bit) from python.org and reopen setup. Your reviews were not changed.");
                }
                if (executable == null)
                    throw new InvalidOperationException("Python was installed but could not be started. Retry setup, or install Python "
                        + String.Join(" or ", supported) + " (64-bit) from python.org.");
            }
            python.Text = executable;
            SetProgress(3, "Compatible Python ready. Preparing setup files…");
            Directory.CreateDirectory(Path.Combine(Desktop.Home, "logs"));
            sessionLog = Path.Combine(Desktop.Home, "logs", "setup-" + DateTime.Now.ToString("yyyyMMdd-HHmmss") + ".log");
            Log("Compatible Python: " + executable);
            extracted = Path.Combine(Path.GetTempPath(), "SystematicSetup-" + Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(extracted);
            await Task.Run(() => ExtractPayload(extracted));
            SetProgress(7, "Setup files ready. Checking your saved reviews…");
            string fileList = Path.Combine(extracted, "additional-files.json");
            File.WriteAllText(fileList, Desktop.Json.Serialize(files), Encoding.UTF8);
            string command = "-I -u " + Desktop.Quote(Path.Combine(extracted, "installer.py"))
                + " --payload " + Desktop.Quote(extracted) + " --home " + Desktop.Quote(Desktop.Home)
                + " --data " + Desktop.Quote(Desktop.Data) + " --files-json " + Desktop.Quote(fileList);
            if (selectedSource.Length > 0) command += " --source " + Desktop.Quote(selectedSource);
            if (useSemantic) command += " --semantic";
            if (deleteOld) command += " --remove-old-versions";
            int exit = await RunProcess(executable, command);
            if (exit != 0) throw new InvalidOperationException("Setup did not finish. Your previous installation remains available.\n\nSee the details below and the setup log:\n" + sessionLog);
            try { Desktop.Shortcut(); }
            catch (Exception error) { Log("Setup succeeded, but the desktop shortcut could not be created: " + error.Message); }
            finished = true;
            SetProgress(100, "Setup complete. Use Start Systematic to open your reviews.");
            primary.Text = "Start Systematic";
            Log("The downloaded setup file can be removed after you have checked your reviews.");
        }
        catch (Exception error)
        {
            Log(error.Message);
            status.Text = "Setup stopped. Review the details below, then retry.";
            MessageBox.Show(this, error.Message, "Systematic setup", MessageBoxButtons.OK, MessageBoxIcon.Information);
        }
        finally
        {
            busy = false; primary.Enabled = true;
            foreach (Control control in setupControls) control.Enabled = !finished;
            // Only the uniquely named temporary payload belongs to this process.
            if (extracted != null && Directory.Exists(extracted)
                && Path.GetDirectoryName(extracted) == Path.GetTempPath().TrimEnd('\\')
                && Path.GetFileName(extracted).StartsWith("SystematicSetup-", StringComparison.Ordinal))
                try { Directory.Delete(extracted, true); } catch { }
        }
    }
    internal static void ExtractPayloadFile(string destination, string name)
    {
        using (Stream resource = Assembly.GetExecutingAssembly().GetManifestResourceStream("payload.zip"))
        using (ZipArchive archive = new ZipArchive(resource, ZipArchiveMode.Read))
        {
            ZipArchiveEntry entry = archive.GetEntry(name);
            if (entry == null) throw new InvalidDataException("Setup files are incomplete: " + name);
            using (Stream input = entry.Open())
            using (FileStream output = File.Create(Path.Combine(destination, name))) input.CopyTo(output);
        }
    }
    internal static void ExtractPayload(string destination)
    {
        using (Stream resource = Assembly.GetExecutingAssembly().GetManifestResourceStream("payload.zip"))
        using (ZipArchive archive = new ZipArchive(resource, ZipArchiveMode.Read))
        foreach (ZipArchiveEntry entry in archive.Entries)
        {
            string target = Path.GetFullPath(Path.Combine(destination, entry.FullName));
            if (!target.StartsWith(Path.GetFullPath(destination) + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase))
                throw new InvalidDataException("Invalid package path.");
            if (String.IsNullOrEmpty(entry.Name)) { Directory.CreateDirectory(target); continue; }
            Directory.CreateDirectory(Path.GetDirectoryName(target));
            using (Stream input = entry.Open())
            using (FileStream output = File.Create(target)) input.CopyTo(output);
        }
    }
    private async Task<int> RunProcess(string executable, string arguments)
    {
        child = new Process { StartInfo = Desktop.StartInfo(executable, arguments), EnableRaisingEvents = true };
        child.OutputDataReceived += delegate(object sender, DataReceivedEventArgs e) { Log(e.Data); };
        child.ErrorDataReceived += delegate(object sender, DataReceivedEventArgs e) { Log(e.Data); };
        child.Start(); child.BeginOutputReadLine(); child.BeginErrorReadLine();
        Process running = child;
        await Task.Run(() => running.WaitForExit());
        int exit = running.ExitCode;
        running.Dispose(); child = null;
        return exit;
    }
    private async void StartApplication()
    {
        busy = true; primary.Enabled = false;
        progress.Text = "Starting…";
        try
        {
            string stateFile = Path.Combine(Desktop.Home, "current.json");
            if (!File.Exists(stateFile))
                throw new InvalidOperationException("Run Systematic Setup first to install this computer.");
            Dictionary<string, object> state = Desktop.Json.Deserialize<Dictionary<string, object>>(File.ReadAllText(stateFile));
            string executable = (string)state["python"];
            string[] versions = Desktop.Strings(state["supported_python"]);
            string supported = await Task.Run(() => Desktop.Probe(executable, versions));
            if (supported == null)
                throw new InvalidOperationException("The installed Python environment is unavailable or incompatible. Run Systematic Setup again to repair it. Your reviews remain in Local AppData.");
            Directory.CreateDirectory(Path.Combine(Desktop.Home, "logs"));
            sessionLog = Path.Combine(Desktop.Home, "logs", "application-" + DateTime.Now.ToString("yyyyMMdd-HHmmss") + ".log");
            status.Text = "Opening your research workspace…";
            int exit = await RunProcess(executable, "-u " + Desktop.Quote((string)state["runner"]) + " --home " + Desktop.Quote(Desktop.Home));
            if (exit != 0) throw new InvalidOperationException("Systematic could not start. See the details below.\n\nLog: " + sessionLog);
            status.Text = "Systematic has stopped.";
            busy = false;
            if (closeAfterStop) { Close(); return; }
        }
        catch (Exception error)
        {
            busy = false; Log(error.Message); status.Text = error.Message.Split('\n')[0];
        }
        progress.Text = "Stopped"; primary.Enabled = true; primary.Text = "Close";
        openBrowser.Enabled = false;
    }
    private void StopApplication()
    {
        if (child == null || child.HasExited) { busy = false; Close(); return; }
        closeAfterStop = true;
        primary.Enabled = false; status.Text = "Finishing active work and closing safely…";
        try { child.StandardInput.WriteLine("stop"); child.StandardInput.Flush(); } catch { }
    }
    private void OnClosing(object sender, FormClosingEventArgs args)
    {
        if (PreviewPath != null || !busy) return;
        args.Cancel = true;
        if (IsSetup)
            MessageBox.Show(this, "Please allow setup to finish. Progress appears in the details box.", "Systematic setup");
        else StopApplication();
    }
}
