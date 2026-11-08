// A relocatable entry point for the bundled runtime, without an installer or console window.
using System;
using System.Diagnostics;
using System.IO;
using System.Reflection;
using System.Text;
using System.Windows.Forms;

internal static class PortableLauncher
{
    // Windows passes a single command line. Preserve quotes, empty arguments and trailing backslashes.
    private static string Quote(string value)
    {
        var result = new StringBuilder("\"");
        int slashes = 0;
        foreach (char c in value)
        {
            if (c == '\\') { slashes++; continue; }
            result.Append('\\', c == '"' ? slashes * 2 + 1 : slashes);
            result.Append(c);
            slashes = 0;
        }
        result.Append('\\', slashes * 2);
        return result.Append('"').ToString();
    }

    [STAThread]
    private static int Main(string[] args)
    {
        try
        {
            string root = Path.GetDirectoryName(Assembly.GetExecutingAssembly().Location);
            string python = Path.Combine(root, "python", "pythonw.exe");
            string script = Path.Combine(root, "decomp-launch.pyw");
            if (!File.Exists(python) || !File.Exists(script))
                throw new FileNotFoundException("Extract the entire portable ZIP before opening decompdecomp.exe. Keep it beside the python and packages folders.");
            var command = new StringBuilder(Quote(script));
            foreach (string arg in args) command.Append(" ").Append(Quote(arg));
            var start = new ProcessStartInfo(python, command.ToString());
            start.UseShellExecute = false;
            start.CreateNoWindow = true;
            // An unrelated Python installation must not change the bundled runtime's imports.
            start.EnvironmentVariables.Remove("PYTHONHOME");
            start.EnvironmentVariables.Remove("PYTHONPATH");
            start.EnvironmentVariables["DECOMP_PORTABLE"] = "1";
            using (Process child = Process.Start(start))
            {
                child.WaitForExit();
                return child.ExitCode;
            }
        }
        catch (Exception error)
        {
            MessageBox.Show(error.Message, "decompdecomp", MessageBoxButtons.OK, MessageBoxIcon.Error);
            return 1;
        }
    }
}
