using Shared.Services;
using System;
using System.IO;
using System.Diagnostics;
using System.IO.Compression;
using System.Threading;
using System.Threading.Tasks;
using Shared.Services.Utilities;

namespace LampaWeb;

public static class LampaCron
{
    static string currentapp;

    public static void Start()
    {
        var init = ModInit.conf;
        _cronTimer = new Timer(cron, null, TimeSpan.Zero, TimeSpan.FromMinutes(Math.Max(init.intervalupdate, 5)));
    }

    public static void Stop()
    {
        _cronTimer.Dispose();
    }

    static Timer _cronTimer;

    static int _updatingDb = 0;

    static async Task<int> ProtectedFrontend(string action, ModuleConf init, string archive = null)
    {
        using var process = new Process();
        var start = process.StartInfo;
        start.FileName = "python3";
        start.UseShellExecute = false;
        start.RedirectStandardError = true;
        start.ArgumentList.Add("-B");
        start.ArgumentList.Add(Path.Combine(ModInit.modpath, "frontend", "install.py"));
        foreach (string argument in new[] { action, init.git, init.tree }) start.ArgumentList.Add(argument ?? "");
        if (archive != null) start.ArgumentList.Add(archive);
        process.Start();
        var diagnostics = await process.StandardError.ReadToEndAsync();
        await process.WaitForExitAsync();
        if (!string.IsNullOrWhiteSpace(diagnostics)) Serilog.Log.Warning("Lampa frontend: {Diagnostics}", diagnostics);
        if (process.ExitCode != 0 && !(action == "check" && process.ExitCode == 10))
            throw new InvalidDataException("Protected Lampa frontend update failed");
        return process.ExitCode;
    }

    async static void cron(object state)
    {
        if (Interlocked.Exchange(ref _updatingDb, 1) == 1)
            return;

        try
        {
            var init = ModInit.conf;
            bool istree = !string.IsNullOrEmpty(init.tree);

            if (File.Exists("data/lampa-frontend-patches.enabled"))
            {
                if (!init.autoupdate || await ProtectedFrontend("check", init) == 0) return;
                byte[] zip = await Http.Download($"https://github.com/{init.git}/archive/{init.tree}.zip");
                if (zip == null) throw new IOException("Pinned Lampa download failed");
                string archive = Path.GetTempFileName();
                try
                {
                    await File.WriteAllBytesAsync(archive, zip);
                    await ProtectedFrontend("install", init, archive);
                }
                finally { File.Delete(archive); }
                return;
            }

            async Task<bool> update()
            {
                if (!init.autoupdate)
                    return false;

                if (!File.Exists("wwwroot/lampa-main/app.min.js"))
                    return true;

                if (istree && File.Exists("wwwroot/lampa-main/tree") && init.tree == File.ReadAllText("wwwroot/lampa-main/tree"))
                    return false;

                if (istree) return true; // Missing or different marker: install before recording the revision.

                bool changeversion = false;

                await Http.GetSpan($"https://raw.githubusercontent.com/{init.git}/{(istree ? init.tree : "main")}/app.min.js", gitapp =>
                {
                    if (!gitapp.Contains("author: 'Yumata'", StringComparison.Ordinal))
                        return;

                    if (currentapp == null)
                        currentapp = CrypTo.md5File("wwwroot/lampa-main/app.min.js");

                    if (!string.IsNullOrEmpty(currentapp) && CrypTo.md5(gitapp) != currentapp)
                        changeversion = true;

                });

                return changeversion;
            }

            if (await update())
            {
                string uri = istree ?
                    $"https://github.com/{init.git}/archive/{init.tree}.zip" :
                    $"https://github.com/{init.git}/archive/refs/heads/main.zip";

                byte[] array = await Http.Download(uri);
                if (array != null)
                {
                    currentapp = null;
                    string repo = init.git.Split('/')[^1];
                    var targetDirectory = $"{repo}-{(istree ? init.tree : "main")}";
                    var isDefaultDirectory = targetDirectory.Equals("lampa-main", StringComparison.OrdinalIgnoreCase);

                    await File.WriteAllBytesAsync("wwwroot/lampa.zip", array);
                    ZipFile.ExtractToDirectory("wwwroot/lampa.zip", "wwwroot/", overwriteFiles: true);

                    if (!isDefaultDirectory)
                    {
                        foreach (string infilePath in Directory.GetFiles($"wwwroot/{targetDirectory}", "*", SearchOption.AllDirectories))
                        {
                            string outfile = infilePath.Replace($"{targetDirectory}", "lampa-main");
                            Directory.CreateDirectory(Path.GetDirectoryName(outfile));
                            File.Copy(infilePath, outfile, true);
                        }
                    }

                    string html = File.ReadAllText("wwwroot/lampa-main/index.html");
                    html = html.Replace("</body>", "<script src=\"/lampainit.js\"></script></body>");

                    html = html.Replace("app.min.js?v=' + cache_version", "app.min.js'");
                    if (!html.Contains("putScript('app.min.js'") || !html.Contains("urlJoin(lampa_url, 'app.min.js')") ||
                        !html.Contains("urlJoin(lampa_url, 'lampa-main/app.min.js')"))
                        Serilog.Log.Warning("Lampa index loader template changed; verify all three app.min.js routes");
                    File.WriteAllText("wwwroot/lampa-main/index.html", html);
                    File.WriteAllText("wwwroot/lampa-main/personal.lampa", "");

                    if (!File.Exists("wwwroot/lampa-main/plugins_black_list.json"))
                        File.WriteAllText("wwwroot/lampa-main/plugins_black_list.json", "[]");

                    if (!File.Exists("wwwroot/lampa-main/plugins/modification.js"))
                    {
                        Directory.CreateDirectory("wwwroot/lampa-main/plugins");
                        File.WriteAllText("wwwroot/lampa-main/plugins/modification.js", string.Empty);
                    }

                    File.Delete("wwwroot/lampa.zip");

                    if (!isDefaultDirectory)
                        Directory.Delete($"wwwroot/{targetDirectory}", true);

                    if (istree) File.WriteAllText("wwwroot/lampa-main/tree", init.tree);
                }
            }
        }
        catch (Exception ex)
        {
            Serilog.Log.Error(ex, "{Class} {CatchId}", "LampaCron", "id_30qftt0j");
        }
        finally
        {
            Volatile.Write(ref _updatingDb, 0);
        }
    }
}
