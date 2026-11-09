using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Runtime.InteropServices;
using System.Threading.Tasks;
using System.Web.Script.Serialization;
using System.Windows.Forms;
using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.WinForms;

internal sealed class DesktopWindow : Form {
    private readonly WebView2 view = new WebView2();
    private readonly Uri appUri;
    private readonly string profile;
    private readonly string iconPath;
    private readonly JavaScriptSerializer json = new JavaScriptSerializer { MaxJsonLength = 90 * 1024 * 1024 };
    private readonly HashSet<string> importing = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
    private readonly List<IntPtr> icons = new List<IntPtr>();
    private bool initialized;
    [DllImport("user32.dll", CharSet=CharSet.Unicode)] private static extern IntPtr LoadImage(IntPtr h, string file, uint type, int cx, int cy, uint flags);
    [DllImport("user32.dll")] private static extern IntPtr SendMessage(IntPtr hwnd, uint msg, IntPtr wp, IntPtr lp);
    [DllImport("user32.dll")] private static extern uint GetDpiForWindow(IntPtr hwnd);
    [DllImport("user32.dll")] private static extern bool DestroyIcon(IntPtr icon);
    [DllImport("shell32.dll", CharSet=CharSet.Unicode)] private static extern int SetCurrentProcessExplicitAppUserModelID(string appId);
    [DllImport("dwmapi.dll")] private static extern int DwmSetWindowAttribute(IntPtr hwnd, int attr, ref int value, int size);

    public DesktopWindow(Uri address, string userProfile, string artwork) {
        appUri = address; profile = userProfile; iconPath = artwork;
        Text = "ExpBot"; StartPosition = FormStartPosition.Manual;
        // Avoid AutoScaleMode.Dpi double-scaling ClientSize under Per-Monitor V2.
        AutoScaleMode = AutoScaleMode.None;
        // Caption shows title only; taskbar still uses the multi-size Icon below.
        ShowIcon = false;
        MinimumSize = new Size(900, 650);
        ApplyDefaultBounds();
        BackColor = SystemDark() ? Color.FromArgb(14, 14, 13) : Color.FromArgb(245, 244, 241);
        SetCurrentProcessExplicitAppUserModelID("ExpBot.Desktop.1.0.0");
        if (File.Exists(iconPath)) Icon = new Icon(iconPath);
        view.Dock = DockStyle.Fill; view.DefaultBackgroundColor = BackColor;
        Controls.Add(view);
        Shown += async delegate { ApplyDefaultBounds(); await StartWebView(); UpdateIcons(); };
        // WinForms resets WM_SETICON to caption-size during Show. Apply after
        // that work so the48px taskbar icon survives the initial window show.
        var iconTimer = new Timer { Interval = 500 }; int iconTicks = 0;
        iconTimer.Tick += delegate { UpdateIcons(); if (++iconTicks == 4) { iconTimer.Stop(); iconTimer.Dispose(); } };
        Shown += delegate { iconTimer.Start(); };
    }
    protected override void OnHandleCreated(EventArgs e) {
        base.OnHandleCreated(e);
        ApplyChromeColors();
        UpdateIcons();
    }
    private void ApplyChromeColors() {
        if (!IsHandleCreated) return;
        bool dark = SystemDark();
        // Match content tokens: dark #0e0e0d / light #f5f4f1 (COLORREF = 0x00BBGGRR).
        int caption = dark ? 0x000D0E0E : 0x00F1F4F5;
        int border = caption;
        int text = dark ? 0x00EEF1F2 : 0x0018191A;
        try {
            // 20 = DWMWA_USE_IMMERSIVE_DARK_MODE (Win10 1809+)
            int immersive = dark ? 1 : 0;
            DwmSetWindowAttribute(Handle, 20, ref immersive, sizeof(int));
        } catch { }
        try {
            // 34/35/36 = DWMWA_BORDER_COLOR / CAPTION_COLOR / TEXT_COLOR (Win11+)
            DwmSetWindowAttribute(Handle, 34, ref border, sizeof(int));
            DwmSetWindowAttribute(Handle, 35, ref caption, sizeof(int));
            DwmSetWindowAttribute(Handle, 36, ref text, sizeof(int));
        } catch { }
        try {
            // Keep form frame in sync so resize edges don't flash a different hue.
            BackColor = dark ? Color.FromArgb(14, 14, 13) : Color.FromArgb(245, 244, 241);
            if (view != null) view.DefaultBackgroundColor = BackColor;
        } catch { }
    }
    protected override void WndProc(ref Message m) {
        base.WndProc(ref m);
        if (m.Msg == 0x02E0 && IsHandleCreated) UpdateIcons();
    }
    private void UpdateIcons() {
        if (!IsHandleCreated) return;
        // Always clear the caption (ICON_SMALL). ShowIcon=false alone is not enough
        // once WM_SETICON has been applied; WinForms / DWM will keep drawing it.
        SendMessage(Handle, 0x80 /* WM_SETICON */, (IntPtr)0 /* ICON_SMALL */, IntPtr.Zero);
        if (!File.Exists(iconPath)) return;
        uint dpi = 96; try { dpi = GetDpiForWindow(Handle); } catch (EntryPointNotFoundException) { }
        // Taskbar only: ICON_BIG. Never re-set ICON_SMALL or the title-bar glyph returns.
        int large = (int)Math.Round(48 * dpi / 96.0);
        IntPtr handle = LoadImage(IntPtr.Zero, iconPath, 1, large, large, 0x10);
        if (handle != IntPtr.Zero) {
            icons.Add(handle);
            SendMessage(Handle, 0x80 /* WM_SETICON */, (IntPtr)1 /* ICON_BIG */, handle);
        }
    }
    private async Task StartWebView() {
        try {
            var options = new CoreWebView2EnvironmentOptions("--disable-features=msEdgeSuperDragDrop,msEdgeSuperDragDropNewUI");
            var env = await CoreWebView2Environment.CreateAsync(null, profile, options);
            await view.EnsureCoreWebView2Async(env);
            view.CoreWebView2.Settings.AreDevToolsEnabled = false;
            view.CoreWebView2.Settings.IsStatusBarEnabled = false;
            view.CoreWebView2.Settings.AreBrowserAcceleratorKeysEnabled = false;
            // Prefer OS theme. Older WebView2 builds ignore this; we still inject
            // a html[data-theme] fallback so dark CSS does not depend on media alone.
            try {
                var profileProp = view.CoreWebView2.GetType().GetProperty("Profile");
                object profileObj = profileProp == null ? null : profileProp.GetValue(view.CoreWebView2, null);
                if (profileObj != null) {
                    var schemeProp = profileObj.GetType().GetProperty("PreferredColorScheme");
                    if (schemeProp != null) {
                        object auto = Enum.Parse(schemeProp.PropertyType, "Auto");
                        schemeProp.SetValue(profileObj, auto, null);
                    }
                }
            } catch { }
            if (SystemDark()) {
                string darkBoot =
                    "document.documentElement.setAttribute('data-theme','dark');" +
                    "document.documentElement.style.colorScheme='dark';" +
                    "try{var m=document.querySelector('meta[name=theme-color]');if(m)m.setAttribute('content','#0e0e0d');}catch(e){}";
                await view.CoreWebView2.AddScriptToExecuteOnDocumentCreatedAsync(darkBoot);
            }
            view.CoreWebView2.NavigationStarting += delegate(object sender, CoreWebView2NavigationStartingEventArgs args) {
                Uri target;
                if (!Uri.TryCreate(args.Uri, UriKind.Absolute, out target)) { args.Cancel = true; return; }
                if (target.IsFile) { args.Cancel = true; QueueFiles(new[] {target.LocalPath}); return; }
                if (!SameOrigin(target)) args.Cancel = true;
            };
            view.CoreWebView2.NewWindowRequested += delegate(object sender, CoreWebView2NewWindowRequestedEventArgs args) {
                args.Handled = true;
                Uri target;
                if (!Uri.TryCreate(args.Uri, UriKind.Absolute, out target)) return;
                if (target.IsFile) QueueFiles(new[] {target.LocalPath});
                else if (SameOrigin(target)) view.CoreWebView2.Navigate(target.AbsoluteUri);
            };
            view.CoreWebView2.DownloadStarting += delegate(object sender, CoreWebView2DownloadStartingEventArgs args) { args.Cancel = true; };
            initialized = true; view.CoreWebView2.Navigate(appUri.AbsoluteUri);
        } catch (Exception ex) {
            File.WriteAllText(Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "desktop-startup-error.txt"), ex.ToString());
            MessageBox.Show("桌面窗口未能启动，请确认 Windows WebView2 Runtime 可用。\n" + ex.Message,
                            "ExpBot", MessageBoxButtons.OK, MessageBoxIcon.Error);
            Close();
        }
    }
    private void ApplyDefaultBounds() {
        Screen screen = IsHandleCreated ? Screen.FromControl(this) : Screen.FromPoint(Cursor.Position);
        Rectangle area = screen.WorkingArea;
        // Default open size is 16:9, as large as will fit in the work area.
        int maxW = Math.Max(960, area.Width * 9 / 10);
        int maxH = Math.Max(540, area.Height * 9 / 10);
        int width = maxW;
        int height = (int)Math.Round(width * 9.0 / 16.0);
        if (height > maxH) {
            height = maxH;
            width = (int)Math.Round(height * 16.0 / 9.0);
        }
        if (width > area.Width) width = area.Width;
        if (height > area.Height) height = area.Height;
        if (width < 960) width = Math.Min(area.Width, 960);
        if (height < 540) height = Math.Min(area.Height, 540);
        int left = area.Left + Math.Max(0, (area.Width - width) / 2);
        int top = area.Top + Math.Max(0, (area.Height - height) / 2);
        SetBounds(left, top, width, height);
        if (IsHandleCreated && (Width < area.Width * 2 / 3 || Height < area.Height * 2 / 3))
            WindowState = FormWindowState.Maximized;
    }
    private static bool SystemDark() {
        try {
            using (var key = Microsoft.Win32.Registry.CurrentUser.OpenSubKey(
                       @"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize")) {
                object value = key == null ? null : key.GetValue("AppsUseLightTheme");
                if (value is int) return ((int)value) == 0;
            }
        } catch { }
        return false;
    }
    private bool SameOrigin(Uri uri) { return uri.Scheme == appUri.Scheme && uri.Host == appUri.Host && uri.Port == appUri.Port; }
    private void FileDragEnter(object sender, DragEventArgs args) {
        args.Effect = args.Data.GetDataPresent(DataFormats.FileDrop) ? DragDropEffects.Copy : DragDropEffects.None;
    }
    private void FileDragDrop(object sender, DragEventArgs args) {
        if (args.Data.GetDataPresent(DataFormats.FileDrop)) QueueFiles((string[])args.Data.GetData(DataFormats.FileDrop));
    }
    private async void QueueFiles(string[] paths) {
        if (!initialized || paths == null || paths.Length == 0) return;
        var items = new List<object>();
        foreach (string input in paths) {
            string path;
            try { path = Path.GetFullPath(input); } catch { continue; }
            if (!importing.Add(path)) continue;
            try {
                string suffix = Path.GetExtension(path).ToLowerInvariant();
                if (Array.IndexOf(new[] {".png",".jpg",".jpeg",".webp",".bmp",".tif",".tiff",".pdf"}, suffix) < 0) continue;
                var file = new FileInfo(path);
                if (!file.Exists || file.Length <= 0 || file.Length > 40L * 1024 * 1024) continue;
                byte[] bytes = await Task.Run(() => File.ReadAllBytes(path));
                items.Add(new { name=file.Name, data=Convert.ToBase64String(bytes) });
            } catch { } finally { importing.Remove(path); }
        }
        if (items.Count > 0 && !IsDisposed) view.CoreWebView2.PostWebMessageAsJson(json.Serialize(new { type="desktop-files", files=items }));
    }
    protected override void Dispose(bool disposing) {
        if (disposing) view.Dispose();
        base.Dispose(disposing);
        foreach (IntPtr icon in icons) DestroyIcon(icon); icons.Clear();
    }
}
internal static class DesktopHost {
    [DllImport("user32.dll")] private static extern bool SetProcessDpiAwarenessContext(IntPtr value);
    [STAThread] private static void Main(string[] args) {
        if (args.Length < 3) return;
        Uri uri;
        if (!Uri.TryCreate(args[0], UriKind.Absolute, out uri) || uri.Scheme != "http" ||
            !(uri.Host == "127.0.0.1" || uri.Host == "localhost")) return;
        try { SetProcessDpiAwarenessContext((IntPtr)(-4)); } catch (EntryPointNotFoundException) { }
        Application.EnableVisualStyles(); Application.SetCompatibleTextRenderingDefault(false);
        try { Application.Run(new DesktopWindow(uri, Path.GetFullPath(args[1]), Path.GetFullPath(args[2]))); }
        catch (Exception error) { File.WriteAllText(Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "desktop-startup-error.txt"), error.ToString()); }
    }
}
