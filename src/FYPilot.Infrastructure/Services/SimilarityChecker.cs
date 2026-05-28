using FYPilot.Application.DTOs;
using FYPilot.Domain.Entities;

namespace FYPilot.Infrastructure.Services;

public static class SimilarityChecker
{
    public static SimilarityResult Check(ProjectIdea idea, List<PreviousProject> previous)
    {
        var results = new List<SimilarProject>();
        foreach (var p in previous)
        {
            var sim = ComputeSimilarity(idea.Title + " " + idea.ProblemStatement,
                p.Title + " " + p.Description);
            if (sim > 20)
                results.Add(new SimilarProject(p.Title, p.Domain, p.Year, sim));
        }
        results = results.OrderByDescending(r => r.SimilarityPct).Take(3).ToList();

        var maxSim = results.Any() ? results.Max(r => r.SimilarityPct) : 0;
        var originality = 100 - maxSim;

        var improvements = new List<string>
        {
            $"Focus on the Lebanese market specificity — add local context to differentiate",
            $"Integrate AI/ML component to increase innovation score",
            $"Add a feature that existing solutions don't have: real-time notifications, Arabic language support",
            $"Target a niche Lebanese sector (e.g., NGOs, clinics) rather than a broad market",
            $"Include a mobile application alongside the web platform"
        };

        return new SimilarityResult(idea.Title, maxSim, originality, results, improvements);
    }

    public static int QuickSimilarity(string title, List<PreviousProject> previous)
    {
        if (!previous.Any()) return 0;
        return previous.Max(p => ComputeSimilarity(title, p.Title));
    }

    private static int ComputeSimilarity(string a, string b)
    {
        var wordsA = Tokenize(a);
        var wordsB = Tokenize(b);
        if (!wordsA.Any() || !wordsB.Any()) return 0;

        var intersection = wordsA.Intersect(wordsB).Count();
        var union = wordsA.Union(wordsB).Count();
        return union > 0 ? (int)((double)intersection / union * 100) : 0;
    }

    private static HashSet<string> Tokenize(string text)
    {
        var stopWords = new HashSet<string> { "a", "an", "the", "for", "and", "or", "in", "of", "to", "with", "is", "are", "be" };
        return text.ToLower()
            .Split(new[] { ' ', '-', '_', ',', '.', ':', ';', '(', ')' }, StringSplitOptions.RemoveEmptyEntries)
            .Where(w => w.Length > 2 && !stopWords.Contains(w))
            .ToHashSet();
    }
}
