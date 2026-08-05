using FYPilot.Domain.Entities;
using FYPilot.Web.Pages.Student;
using Xunit;

namespace FYPilot.Tests.Roadmap;

/// <summary>
/// RoadmapModel.ClassifyPhaseTimeline is the display-only containment fix
/// for the Gantt bug where several overflowing phases all rendered at the
/// same clamped final target week (e.g. three different phases all showing
/// "Week 16"). It must never mutate the phases it classifies, never invent
/// exact week numbers beyond what each phase's own EstimatedWeeks already
/// implies, and never collapse distinct overflowing phases onto the same
/// display week.
/// </summary>
public class RoadmapPhaseTimelineTests
{
    private static RoadmapPhase Phase(int number, int estimatedWeeks, string name = "") =>
        new()
        {
            Id = number,
            PhaseNumber = number,
            Name = string.IsNullOrEmpty(name) ? $"Phase {number}" : name,
            EstimatedWeeks = estimatedWeeks,
        };

    [Fact]
    public void AllPhasesFitting_AreAllWithinTarget()
    {
        var phases = new List<RoadmapPhase> { Phase(1, 4), Phase(2, 4), Phase(3, 4) };

        var timeline = RoadmapModel.ClassifyPhaseTimeline(phases, targetWeeks: 16);

        Assert.All(timeline, entry => Assert.Equal(RoadmapPhaseTimelineStatus.WithinTarget, entry.Status));
        Assert.DoesNotContain(timeline, entry =>
            entry.Status is RoadmapPhaseTimelineStatus.PartiallyBeyondTarget
                or RoadmapPhaseTimelineStatus.BeyondTarget);
    }

    [Fact]
    public void PhaseStartingWithinTargetButEndingBeyondIt_IsPartiallyBeyondTarget()
    {
        // Target = 10 weeks. Phase 1 takes weeks 1-8, phase 2 takes weeks
        // 9-14 -- starts inside the target (week 9) but its own 6-week
        // duration runs past it (ends week 14).
        var phases = new List<RoadmapPhase> { Phase(1, 8), Phase(2, 6) };

        var timeline = RoadmapModel.ClassifyPhaseTimeline(phases, targetWeeks: 10);

        Assert.Equal(RoadmapPhaseTimelineStatus.WithinTarget, timeline[0].Status);
        Assert.Equal(RoadmapPhaseTimelineStatus.PartiallyBeyondTarget, timeline[1].Status);
        Assert.Equal(9, timeline[1].DisplayStartWeek);
        Assert.Equal(14, timeline[1].DisplayEndWeek);
        // Rendered (within-target) portion is clamped to the target boundary.
        Assert.Equal(10, timeline[1].WithinTargetEndWeek);
    }

    [Fact]
    public void PhasesEntirelyAfterTarget_AreBeyondTargetInCanonicalOrder()
    {
        var phases = new List<RoadmapPhase> { Phase(1, 10), Phase(2, 3), Phase(3, 3) };

        var timeline = RoadmapModel.ClassifyPhaseTimeline(phases, targetWeeks: 10);

        Assert.Equal(RoadmapPhaseTimelineStatus.WithinTarget, timeline[0].Status);
        Assert.Equal(RoadmapPhaseTimelineStatus.BeyondTarget, timeline[1].Status);
        Assert.Equal(RoadmapPhaseTimelineStatus.BeyondTarget, timeline[2].Status);
        // Canonical (PhaseNumber) order is preserved, not reordered.
        Assert.Equal([1, 2, 3], timeline.Select(t => t.Phase.PhaseNumber));
    }

    [Fact]
    public void MultipleOverflowPhases_NeverShareTheSameDisplayWeek()
    {
        // Reproduces the exact reported bug shape: three phases after a
        // 16-week target must NOT all be labeled "Week 16".
        var phases = new List<RoadmapPhase>
        {
            Phase(1, 16, "Requirements through core work"),
            Phase(2, 1, "Core Feature Development"),
            Phase(3, 1, "Testing and Bug Fixing"),
            Phase(4, 1, "Documentation and Final Deployment"),
        };

        var timeline = RoadmapModel.ClassifyPhaseTimeline(phases, targetWeeks: 16);

        var overflowStartWeeks = timeline
            .Where(t => t.Status == RoadmapPhaseTimelineStatus.BeyondTarget)
            .Select(t => t.DisplayStartWeek)
            .ToList();

        Assert.Equal(3, overflowStartWeeks.Count);
        Assert.Equal(overflowStartWeeks.Count, overflowStartWeeks.Distinct().Count());
        Assert.Equal([17, 18, 19], overflowStartWeeks);
    }

    [Theory]
    [InlineData(0)]
    [InlineData(-3)]
    public void NonPositiveEstimatedWeeks_IsClassifiedNeedsReview_WithoutCrashing(int invalidWeeks)
    {
        var phases = new List<RoadmapPhase> { Phase(1, 4), Phase(2, invalidWeeks), Phase(3, 4) };

        var timeline = RoadmapModel.ClassifyPhaseTimeline(phases, targetWeeks: 16);

        Assert.Equal(RoadmapPhaseTimelineStatus.NeedsReview, timeline[1].Status);
        // A NeedsReview phase must never be silently force-fit into the
        // final target week -- its display range is not asserted as a real
        // week number.
        Assert.Equal(0, timeline[1].DisplayStartWeek);
        // Phases after it still classify normally (the cursor is not
        // corrupted by skipping the invalid entry).
        Assert.Equal(RoadmapPhaseTimelineStatus.WithinTarget, timeline[2].Status);
        Assert.Equal(5, timeline[2].DisplayStartWeek);
    }

    [Fact]
    public void TargetSixteenWeeks_WithPhasesTotallingTwenty_SplitsIntoMainAndOverflow()
    {
        var phases = new List<RoadmapPhase>
        {
            Phase(1, 16), // fits exactly
            Phase(2, 2),  // beyond target
            Phase(3, 2),  // beyond target
        };

        var timeline = RoadmapModel.ClassifyPhaseTimeline(phases, targetWeeks: 16);

        var mainTimeline = timeline
            .Where(t => t.Status is RoadmapPhaseTimelineStatus.WithinTarget
                or RoadmapPhaseTimelineStatus.PartiallyBeyondTarget)
            .ToList();
        var overflowTimeline = timeline
            .Where(t => t.Status is RoadmapPhaseTimelineStatus.PartiallyBeyondTarget
                or RoadmapPhaseTimelineStatus.BeyondTarget
                or RoadmapPhaseTimelineStatus.NeedsReview)
            .ToList();

        Assert.Single(mainTimeline);
        Assert.Equal(2, overflowTimeline.Count);

        // "Estimated total duration" honesty check: target(16) +
        // recommendedAdditionalWeeks should be representable from the same
        // authoritative EstimatedWeeks data an overflow section would show
        // (the overflow phases collectively span weeks 17-20 => 4 extra
        // weeks), not a fabricated number.
        var actualScheduledWeeks = timeline.Max(t => t.DisplayEndWeek);
        Assert.Equal(20, actualScheduledWeeks);
    }

    [Fact]
    public void ExactFitDuration_ProducesNoOverflowEntries()
    {
        var phases = new List<RoadmapPhase> { Phase(1, 6), Phase(2, 6), Phase(3, 4) };

        var timeline = RoadmapModel.ClassifyPhaseTimeline(phases, targetWeeks: 16);

        Assert.All(timeline, entry => Assert.Equal(RoadmapPhaseTimelineStatus.WithinTarget, entry.Status));
        Assert.Equal(16, timeline.Last().DisplayEndWeek);
    }

    [Fact]
    public void ClassificationIsPureAndDoesNotMutatePersistedPhases()
    {
        var phases = new List<RoadmapPhase> { Phase(1, 10), Phase(2, 10) };
        var originalEstimatedWeeks = phases.Select(p => p.EstimatedWeeks).ToList();
        var originalNames = phases.Select(p => p.Name).ToList();

        _ = RoadmapModel.ClassifyPhaseTimeline(phases, targetWeeks: 8);

        Assert.Equal(originalEstimatedWeeks, phases.Select(p => p.EstimatedWeeks));
        Assert.Equal(originalNames, phases.Select(p => p.Name));
        // No new properties on RoadmapPhase exist for timeline data -- the
        // entity shape itself is untouched by this display-only feature.
        Assert.Equal(2, phases.Count);
    }
}
