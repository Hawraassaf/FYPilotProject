using System.Text.Json;
using FYPilot.Application.DTOs;
using FYPilot.Domain.Entities;

namespace FYPilot.Infrastructure.Services;

public static class FeasibilityAnalyzer
{
    public static FeasibilityReport Analyze(ProjectIdea idea, List<StudentSkill> skills, StudentProfile? profile)
    {
        var required = idea.RequiredSkills.Split(", ", StringSplitOptions.RemoveEmptyEntries).ToList();
        var owned = skills.Select(s => s.SkillName).ToList();

        // Skill Match Score
        var matching = required.Count(r => owned.Contains(r));
        var skillMatch = required.Count > 0 ? (int)((double)matching / required.Count * 100) : 70;

        // Difficulty Match
        var levelMap = new Dictionary<string, int> { ["beginner"] = 1, ["intermediate"] = 2, ["advanced"] = 3 };
        var diffMap = new Dictionary<string, int> { ["beginner"] = 1, ["intermediate"] = 2, ["advanced"] = 3 };
        var userLevel = levelMap.GetValueOrDefault(profile?.ExperienceLevel ?? "intermediate", 2);
        var ideaDiff = diffMap.GetValueOrDefault(idea.DifficultyLevel, 2);
        var diffMatch = ideaDiff == userLevel ? 100 : ideaDiff == userLevel + 1 ? 75 : ideaDiff == userLevel - 1 ? 90 : 50;

        // Timeline Fit
        var hoursPerWeek = profile?.AvailableHoursPerWeek ?? 20;
        var totalHours = idea.ExpectedDurationWeeks * hoursPerWeek;
        var timelineFit = totalHours >= 200 ? 90 : totalHours >= 150 ? 75 : 60;

        // Market Score
        var marketScore = idea.MarketDemandScore;

        // Innovation
        var innovationScore = idea.InnovationScore;

        // Risk Score (lower = riskier)
        var missingCount = idea.MissingSkills.Split(", ", StringSplitOptions.RemoveEmptyEntries).Length;
        var riskScore = Math.Max(20, 100 - (missingCount * 15));

        // Final = weighted average
        var final = (int)(skillMatch * 0.25 + diffMatch * 0.15 + timelineFit * 0.15 + marketScore * 0.20 + innovationScore * 0.15 + riskScore * 0.10);

        var risks = BuildRisks(idea, skills, profile, missingCount);

        return new FeasibilityReport
        {
            SkillMatchScore = skillMatch,
            DifficultyMatchScore = diffMatch,
            TimelineFitScore = timelineFit,
            MarketUsefulnessScore = marketScore,
            InnovationScore = innovationScore,
            RiskScore = riskScore,
            FinalFeasibilityScore = final,
            Explanation = BuildExplanation(final, skillMatch, diffMatch, missingCount),
            RisksJson = JsonSerializer.Serialize(risks),
        };
    }

    private static List<RiskItem> BuildRisks(ProjectIdea idea, List<StudentSkill> skills, StudentProfile? profile, int missingCount)
    {
        var risks = new List<RiskItem>();

        if (missingCount > 2)
            risks.Add(new RiskItem("high", "Skill Gap", $"Missing {missingCount} required skills: {idea.MissingSkills}",
                "Allocate 2-3 weeks for skill upskilling via online courses before starting."));

        if (idea.ExpectedDurationWeeks > 16 && (profile?.AvailableHoursPerWeek ?? 20) < 20)
            risks.Add(new RiskItem("high", "Timeline", "Project scope exceeds available time.",
                "Reduce scope to core MVP features. Focus on 3-4 key features."));

        if (idea.DifficultyLevel == "advanced" && profile?.ExperienceLevel == "beginner")
            risks.Add(new RiskItem("medium", "Complexity Mismatch", "Project is advanced but student level is beginner.",
                "Start with simpler sub-components. Get a supervisor with relevant expertise."));

        if (idea.Domain.Contains("AI") || idea.Domain.Contains("Data Science"))
            risks.Add(new RiskItem("medium", "Data Availability", "AI/ML projects require quality datasets.",
                "Identify datasets early. Use Kaggle, UCI Repository, or collect Lebanese-specific data."));

        risks.Add(new RiskItem("low", "Technical", "Integration between frontend and backend may be complex.",
            "Use API-first development. Document endpoints before coding."));

        risks.Add(new RiskItem("low", "Deployment", "Deployment to production environment may face issues.",
            "Use Docker + cloud deployment. Test in staging environment first."));

        return risks;
    }

    private static string BuildExplanation(int final, int skillMatch, int diffMatch, int missingCount)
    {
        var verdict = final >= 80 ? "Excellent fit" : final >= 65 ? "Good fit" : final >= 50 ? "Moderate fit" : "Challenging fit";
        return $"{verdict} (Score: {final}/100). Your skill match is {skillMatch}% with {missingCount} skills to acquire. " +
               $"Difficulty alignment score is {diffMatch}%. " +
               (final >= 70 ? "This project is well-suited to your current profile." : "Consider upskilling or choosing a slightly simpler project.");
    }
}
