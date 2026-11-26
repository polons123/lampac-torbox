using Shared;
using Shared.Services;
using Shared.Models.AppConf;
using Shared.Models.Events;
using Shared.Models.Module;
using Shared.Models.Module.Interfaces;
using System.Collections.Generic;
using System.IO;

namespace Tracks;

public class ModInit : IModuleLoaded
{
    public static string modpath;
    public static ModuleConf conf;

    public void Loaded(InitspaceModel initspace)
    {
        modpath = initspace.path;

        updateConf();
        EventListener.UpdateInitFile += updateConf;

        foreach (var m in conf.limit_map)
            CoreInit.conf.WAF.limit_map.Insert(0, m);

        var directory = Directory.CreateDirectory("database/tracks");
        // A killed process cannot run its finally block. Only remove abandoned probe writes.
        try
        {
            foreach (var pending in directory.EnumerateFiles("*.tmp"))
                if (pending.LastWriteTimeUtc < System.DateTime.UtcNow.AddDays(-1) &&
                    System.Text.RegularExpressions.Regex.IsMatch(pending.Name, @"^[a-f0-9]{32}(\.[a-f0-9]{32})?\.tmp$"))
                    pending.Delete();
        }
        catch (System.Exception ex) when (ex is IOException or System.UnauthorizedAccessException)
        {
            Serilog.Log.Warning("Tracks cache cleanup failed ({ErrorType})", ex.GetType().Name);
        }
    }

    public void Dispose()
    {
        EventListener.UpdateInitFile -= updateConf;
    }

    void updateConf()
    {
        conf = ModuleInvoke.Init("Tracks", new ModuleConf()
        {
            tsuri = null,
            tsstat = null,
            tsauth = null,
            limit_map = new List<WafLimitRootMap>()
            {
                new("^/ffprobe", new WafLimitMap { limit = 10, second = 1 })
            }
        });
    }
}
