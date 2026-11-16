using Microsoft.AspNetCore.Http;
using Shared;
using Shared.Models.Base;
using Shared.Models.Events;
using Shared.Models.Module;
using Shared.Models.Module.Interfaces;
using System.Collections.Generic;
namespace TorBox;
public class ModInit : IModuleLoaded, IModuleOnline
{
    public List<ModuleOnlineItem> Invoke(HttpContext httpContext, RequestModel requestInfo, string host, OnlineEventsModel args)
    {
        return new List<ModuleOnlineItem> { new(new BaseSettings { plugin = "TorBox", enable = true, enabled = true, displayname = "TorBox", displayindex = 552 }, "torbox", "TorBox") };
    }
    public void Loaded(InitspaceModel initspace) {
        // Named Infuse URLs contain decoded spaces/Unicode; tokens and filenames are checked by the controller.
        CoreInit.BaseModPathWhiteList.Add("/torbox/series/external/");
    }
    public void Dispose() { }
}
