using System.Security.Cryptography;
using System.Text;

namespace FYPilot.Application.Common;

/// <summary>
/// Computes the stable RequestHash used by AiAgentJob for duplicate-job
/// reuse and relevance-scoped result lookups (see AiAgentJobService).
///
/// The hash covers only normalized, already-authorized inputs (e.g. sorted
/// idea ids the caller already validated ownership of) -- never raw prompt
/// text, provider keys, or anything the caller hasn't already checked.
/// Callers are responsible for building AiAgentJob.RequestJson separately
/// from the same safe inputs.
/// </summary>
public static class AiAgentJobRequestHasher
{
    public static string ComputeHash(string agentName, int? projectId, IEnumerable<string> normalizedInputs)
    {
        var ordered = normalizedInputs.OrderBy(value => value, StringComparer.Ordinal);
        var canonical = $"{agentName}|{projectId?.ToString() ?? "null"}|{string.Join(",", ordered)}";

        var bytes = SHA256.HashData(Encoding.UTF8.GetBytes(canonical));
        return Convert.ToHexString(bytes).ToLowerInvariant();
    }
}
