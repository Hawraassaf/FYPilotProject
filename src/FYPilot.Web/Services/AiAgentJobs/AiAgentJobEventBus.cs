using System.Collections.Concurrent;
using System.Threading.Channels;

namespace FYPilot.Web.Services.AiAgentJobs;

/// <summary>
/// The event payload AiAgentJobCoordinator publishes and the SSE endpoint
/// (GET /api/ai-agent-jobs/{jobId}/events) forwards to the browser as
/// `id: {Sequence}\ndata: {json}\n\n`. StageStates is the FULL authoritative
/// map (never a delta) -- see AiAgentJob.StageStatesJson.
/// </summary>
public sealed record AiAgentJobEventDto(
    long Sequence,
    string Type, // "progress" | "job_completed" | "job_failed" | "job_cancelled"
    string Status,
    string StageKey,
    Dictionary<string, string> StageStates,
    string Message,
    string? Provider,
    string? Model,
    int CurrentAttemptChunkCount,
    int? CurrentAttemptTokenCount,
    string? ErrorCode,
    string? ErrorMessage);

/// <summary>
/// In-process broadcast pub/sub for live job events, one Channel per
/// SUBSCRIBER (not one shared Channel per job) so multiple browser tabs
/// watching the same job each receive every event instead of competing
/// consumers each seeing only a subset. Publishing is a pure side effect of
/// AiAgentJobCoordinator's work -- nothing about mirroring or finalizing a
/// job depends on a subscriber existing; a Publish() with zero subscribers
/// is a harmless no-op.
/// </summary>
public interface IAiAgentJobEventBus
{
    Guid Subscribe(Guid jobId, out ChannelReader<AiAgentJobEventDto> reader);
    void Unsubscribe(Guid jobId, Guid subscriberId);
    void Publish(Guid jobId, AiAgentJobEventDto evt);
}

public sealed class AiAgentJobEventBus : IAiAgentJobEventBus
{
    private readonly ConcurrentDictionary<Guid, ConcurrentDictionary<Guid, Channel<AiAgentJobEventDto>>> _subscribersByJob = new();

    public Guid Subscribe(Guid jobId, out ChannelReader<AiAgentJobEventDto> reader)
    {
        var subscriberId = Guid.NewGuid();

        var channel = Channel.CreateUnbounded<AiAgentJobEventDto>(new UnboundedChannelOptions
        {
            SingleReader = true,
            SingleWriter = false,
        });

        var subscribers = _subscribersByJob.GetOrAdd(jobId, static _ => new ConcurrentDictionary<Guid, Channel<AiAgentJobEventDto>>());
        subscribers[subscriberId] = channel;

        reader = channel.Reader;
        return subscriberId;
    }

    public void Unsubscribe(Guid jobId, Guid subscriberId)
    {
        if (!_subscribersByJob.TryGetValue(jobId, out var subscribers))
        {
            return;
        }

        if (subscribers.TryRemove(subscriberId, out var channel))
        {
            channel.Writer.TryComplete();
        }

        if (subscribers.IsEmpty)
        {
            _subscribersByJob.TryRemove(jobId, out _);
        }
    }

    public void Publish(Guid jobId, AiAgentJobEventDto evt)
    {
        if (!_subscribersByJob.TryGetValue(jobId, out var subscribers))
        {
            return;
        }

        foreach (var channel in subscribers.Values)
        {
            // Unbounded writer -- TryWrite never blocks or fails except on
            // a completed channel, which only happens after Unsubscribe.
            channel.Writer.TryWrite(evt);
        }
    }
}
