using Shared.Models.Module;

namespace Tracks;

public class ModuleConf : ModuleBaseConf
{
    public int ffprobe_timeout_seconds { get; set; } = 30;

    public string tsuri { get; set; }

    public string tsstat { get; set; }

    public string tsauth { get; set; }
}
