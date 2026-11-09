using System;
using System.Diagnostics;
using System.IO;
using System.Windows.Forms;

internal static class Launcher {
    [STAThread]
    private static void Main() {
        string root = AppDomain.CurrentDomain.BaseDirectory;
        string python = Path.Combine(root, "runtime", "pythonw.exe");
        string script = Path.Combine(root, "run.py");
        if (!File.Exists(python) || !File.Exists(script)) {
            MessageBox.Show("Please extract the entire application folder first.\nDo not move this launcher away from runtime and app folders.", "ExpBot", MessageBoxButtons.OK, MessageBoxIcon.Information);
            return;
        }
        try {
            var info = new ProcessStartInfo(python, "\"" + script + "\"");
            info.WorkingDirectory = root;
            info.UseShellExecute = false;
            info.CreateNoWindow = true;
            Process.Start(info);
        } catch (Exception ex) {
            MessageBox.Show("The application could not start.\n" + ex.Message, "ExpBot", MessageBoxButtons.OK, MessageBoxIcon.Error);
        }
    }
}
