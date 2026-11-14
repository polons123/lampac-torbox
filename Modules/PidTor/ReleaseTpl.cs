using Shared.Models.Templates;
using Shared.Services.Pools;
using System;
using System.Collections.Generic;
using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;
using System.Text.RegularExpressions;

namespace PidTor;

// Retain the standard native/HTML menu, adding provider metadata to its rows only.
public class ReleaseTpl(ITplResult inner, List<object> metadata) : ITplResult
{
    public bool IsEmpty => inner.IsEmpty;
    public int Length => inner.Length;
    public string Type => inner.Type;
    public object ToObject() => JsonNode.Parse(ToJson());
    JsonObject Row(JsonObject row, int index)
    {
        if (index < metadata.Count)
            row["pidtor"] = JsonSerializer.SerializeToNode(metadata[index]);
        return row;
    }
    public string ToJson()
    {
        var root = JsonNode.Parse(inner.ToJson()).AsObject();
        if (root["data"] is JsonArray rows)
            for (int i = 0; i < rows.Count; i++)
                Row(rows[i].AsObject(), i);
        return root.ToJsonString();
    }
    public string ToHtml()
    {
        int index = 0;
        return Regex.Replace(inner.ToHtml(), "data-json='([^']*)'", m =>
            "data-json='" + Row(JsonNode.Parse(m.Groups[1].Value).AsObject(), index++).ToJsonString() + "'");
    }
    public StringBuilder ToBuilderJson() => StringBuilderPool.Rent().Append(ToJson());
    public StringBuilder ToBuilderHtml() => StringBuilderPool.Rent().Append(ToHtml());
}
