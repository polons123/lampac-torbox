using Shared;
using Shared.Models.Module;
using Shared.Models.Module.Interfaces;

namespace Infuse;
public class ModInit : IModuleLoaded
{
    public void Loaded(InitspaceModel initspace) => CoreInit.BaseModPathWhiteList.Add("/infuse/file/");
    public void Dispose() { }
}
