namespace FYPilot.Application.Common;

/// <summary>
/// Single source of truth for turning a 0-100 idea-generation score into a
/// consistent label and overall figure. Used by both Idea Generator and
/// Idea Comparison so the same score is never labeled two different ways.
/// A score of 0 means "not evaluated" (older ideas, or a value the AI
/// service never returned) -- it is never a real earned score, since every
/// score ProjectIdeaAgent computes is clamped to at least 30-40.
/// </summary>
public static class ScoreHelper
{
    public static bool IsEvaluated(int score) => score > 0;

    /// <summary>80-100 High, 60-79 Good, 40-59 Moderate, 0-39 Low.</summary>
    public static string Label(int score) => score switch
    {
        >= 80 => "High",
        >= 60 => "Good",
        >= 40 => "Moderate",
        _ => "Low"
    };

    /// <summary>
    /// Rounded mean of the given scores, ignoring any that are "not
    /// evaluated" (0). Returns null when none of the scores are available,
    /// so the caller can show "Not evaluated" instead of a fabricated 0.
    /// </summary>
    public static int? Overall(params int[] scores)
    {
        var available = scores.Where(IsEvaluated).ToList();

        return available.Count == 0
            ? null
            : (int)Math.Round(available.Average());
    }
}
