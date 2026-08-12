using System.Text.Json;
using FYPilot.Domain.Entities;
using FYPilot.Web.Pages.Student;
using Xunit;

namespace FYPilot.Tests.Roadmap;

/// <summary>
/// RoadmapModel.DiagnosePhaseCapacityWarnings closes the gap where Python's
/// capacity_scheduler.diagnose_phase_capacity (services/FYPilot.AI) computes
/// a correct phase-local capacity warning at generation time, but this page
/// recomputes WorkloadSummary from the PERSISTED phases on every reload (see
/// RoadmapModel.ComputeWorkloadSummary) -- so that one-time warning was
/// silently lost on redisplay. Confirmed live shape: a phase reporting only
/// "1 week" while carrying far more hours than a single week can hold, with
/// the whole-project summary still reading as only modestly over capacity.
/// Must never mutate the phases it inspects.
/// </summary>
public class RoadmapPhaseCapacityWarningsTests
{
    // The exact 12 task-effort figures from the confirmed live defect
    // (Arabic Medical Symptom Triage Assistant roadmap, "Phase 10"): 12
    // tasks, 103h total, reported as "Week 16, Duration: 1 week" for a
    // 1-person/20h-per-week team -- mirrors
    // tests/test_roadmap_phase_capacity_and_deliverable_dedup.py's
    // _LIVE_PHASE_10_TASK_HOURS on the Python side.
    private static readonly int[] LivePhaseTaskHours = [8, 10, 7, 7, 10, 4, 8, 15, 7, 16, 6, 5];

    private static RoadmapPhase Phase(int number, int estimatedWeeks, IEnumerable<int> taskHours, string name = "") =>
        new()
        {
            Id = number,
            PhaseNumber = number,
            Name = string.IsNullOrEmpty(name) ? $"Phase {number}" : name,
            EstimatedWeeks = estimatedWeeks,
            TasksJson = JsonSerializer.Serialize(
                taskHours.Select((hours, index) => new
                {
                    title = $"Task {index + 1}",
                    estimatedHours = hours,
                })),
        };

    [Fact]
    public void OneWeekPhaseWith103Hours_IsFlaggedAsCapacityViolation()
    {
        var phases = new List<RoadmapPhase>
        {
            Phase(10, estimatedWeeks: 1, LivePhaseTaskHours, name: "Final Integration and Testing"),
        };

        var warnings = RoadmapModel.DiagnosePhaseCapacityWarnings(
            phases, teamSize: 1, hoursPerWeekPerMember: 20).ToList();

        Assert.Single(warnings);
        Assert.Contains("Final Integration and Testing", warnings[0]);
        Assert.Contains("103h", warnings[0]);
        Assert.Contains("cannot fit", warnings[0]);
        // ceil(103 / 20) = 6
        Assert.Contains("at least 6 week(s)", warnings[0]);
    }

    [Fact]
    public void OverloadedTerminalPhase_IsStillFlaggedEvenWhenWholeProjectReadsFeasible()
    {
        // Reproduces the confirmed live PDF shape: a 9-phase roadmap where
        // the overall project utilization looks only modestly over capacity
        // (368h planned / 320h capacity = 115%) while the terminal phase
        // alone (132h squeezed into 1 week) is individually impossible --
        // 660% of that single week's real capacity.
        var phases = new List<RoadmapPhase>
        {
            Phase(1, 1, [8, 6, 4]),
            Phase(2, 1, [6, 3, 5]),
            Phase(3, 2, [8, 10, 4]),
            Phase(4, 2, [14, 9]),
            Phase(5, 3, [12, 8, 24, 3]),
            Phase(6, 2, [12, 10, 10, 6]),
            Phase(7, 2, [10, 8, 6]),
            Phase(8, 3, [22, 16, 12]),
            Phase(9, 1, [132], name: "FastAPI Triage Service and Razor Pages Integration"),
        };

        var warnings = RoadmapModel.DiagnosePhaseCapacityWarnings(
            phases, teamSize: 1, hoursPerWeekPerMember: 20).ToList();

        Assert.Single(warnings);
        Assert.Contains("FastAPI Triage Service and Razor Pages Integration", warnings[0]);
        Assert.Contains("132h", warnings[0]);
        // ceil(132 / 20) = 7
        Assert.Contains("at least 7 week(s)", warnings[0]);
    }

    [Fact]
    public void FortyHourPhaseAcrossTwoWeeks_PassesAtTwentyHoursPerWeek()
    {
        var phases = new List<RoadmapPhase> { Phase(1, estimatedWeeks: 2, [20, 20]) };

        var warnings = RoadmapModel.DiagnosePhaseCapacityWarnings(
            phases, teamSize: 1, hoursPerWeekPerMember: 20);

        Assert.Empty(warnings);
    }

    [Fact]
    public void DiagnosingCapacity_NeverMutatesPhaseHoursOrDuration()
    {
        var phases = new List<RoadmapPhase>
        {
            Phase(10, estimatedWeeks: 1, LivePhaseTaskHours, name: "Final Integration and Testing"),
        };

        _ = RoadmapModel.DiagnosePhaseCapacityWarnings(phases, 1, 20).ToList();

        Assert.Equal(1, phases[0].EstimatedWeeks);
        Assert.Equal(103, RoadmapModel.ParseTasks(phases[0].TasksJson).Sum(t => t.EstimatedHours ?? 0));
    }

    [Fact]
    public void NonOverloadedProject_ReportsNoPhaseCapacityWarnings()
    {
        var phases = new List<RoadmapPhase>
        {
            Phase(1, estimatedWeeks: 1, [8, 6, 4]),
            Phase(2, estimatedWeeks: 2, [10, 10]),
        };

        var warnings = RoadmapModel.DiagnosePhaseCapacityWarnings(
            phases, teamSize: 1, hoursPerWeekPerMember: 20);

        Assert.Empty(warnings);
    }
}
