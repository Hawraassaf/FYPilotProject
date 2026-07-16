using FYPilot.Application.DTOs;
using FYPilot.Application.Interfaces;
using FYPilot.Infrastructure.Data;
using Microsoft.AspNetCore.Mvc;
using Microsoft.AspNetCore.Mvc.RazorPages;
using Microsoft.EntityFrameworkCore;
using Microsoft.AspNetCore.Authorization;
namespace FYPilot.Web.Pages;

[Authorize(Roles = "admin")]
public class SystemTestModel(ApplicationDbContext db, IAiServiceClient ai) : PageModel
{
    public void OnGet() { }

    public async Task<IActionResult> OnPostRunAllAsync()
    {
        var results = new List<object>();

        // ── Database ─────────────────────────────────────────────────────────
        try
        {
            var count = await db.Users.CountAsync();
            results.Add(new { id = "database", status = "ok", detail = $"Connected — {count} users in DB" });
        }
        catch (Exception ex)
        {
            results.Add(new { id = "database", status = "error", detail = ex.Message });
        }

        // ── AI health ────────────────────────────────────────────────────────
        AiHealthResponse? health = null;
        try
        {
            health = await ai.GetHealthAsync();
            results.Add(health != null
                ? new { id = "ai-health", status = "ok",    detail = health.Status }
                : new { id = "ai-health", status = "error", detail = "AI service returned null — is Python running on :8000?" });
        }
        catch (Exception ex)
        {
            results.Add(new { id = "ai-health", status = "error", detail = ex.Message });
        }

        // ── Skill analysis ───────────────────────────────────────────────────
        try
        {
            var resp = await ai.AnalyzeSkillsAsync(new SkillAnalysisRequest(["Python", "C#", "SQL"], "intermediate"));
            results.Add(resp != null
                ? new { id = "skill-analysis", status = "ok",    detail = $"Score: {resp.SkillScore} / Level: {resp.RecommendedLevel}" }
                : new { id = "skill-analysis", status = "error", detail = "Null response from AI service" });
        }
        catch (Exception ex)
        {
            results.Add(new { id = "skill-analysis", status = "error", detail = ex.Message });
        }

        // ── Feasibility prediction ────────────────────────────────────────────
        try
        {
            var resp = await ai.PredictFeasibilityAsync(new FeasibilityPredictionRequest(70, 1, 16, 3, 2, false, false, false, 80, 75));
            results.Add(resp != null
                ? new { id = "feasibility", status = "ok",    detail = $"Score: {resp.FeasibilityScore}% | Risk: {resp.RiskLevel}" }
                : new { id = "feasibility", status = "error", detail = "Null response from AI service" });
        }
        catch (Exception ex)
        {
            results.Add(new { id = "feasibility", status = "error", detail = ex.Message });
        }

        // ── Similarity check ──────────────────────────────────────────────────
        try
        {
            var resp = await ai.CheckSimilarityAsync(new SimilarityCheckRequest("Smart Healthcare Appointment System", "A system to manage medical appointments"));
            results.Add(resp != null
                ? new { id = "similarity", status = "ok",    detail = $"Similarity: {resp.SimilarityScore}% | Originality: {resp.OriginalityScore}%" }
                : new { id = "similarity", status = "error", detail = "Null response from AI service" });
        }
        catch (Exception ex)
        {
            results.Add(new { id = "similarity", status = "error", detail = ex.Message });
        }

        // ── Market matching ───────────────────────────────────────────────────
        try
        {
            var resp = await ai.MatchMarketAsync(new MarketMatchRequest("Smart Clinic Booking", "AI-powered appointment scheduling for Lebanese clinics", "Healthcare"));
            results.Add(resp != null
                ? new { id = "market-match", status = "ok",    detail = $"Relevance: {resp.MarketRelevanceScore}% | Sector: {resp.BestMatchSector}" }
                : new { id = "market-match", status = "error", detail = "Null response from AI service" });
        }
        catch (Exception ex)
        {
            results.Add(new { id = "market-match", status = "error", detail = ex.Message });
        }

        // ── Risk alarms ───────────────────────────────────────────────────────
        try
        {
            var resp = await ai.GetRiskAlarmsAsync(new RiskAlarmRequest(65, 2, 14, 3, false, false));
            results.Add(resp != null
                ? new { id = "risk-alarms", status = "ok",    detail = $"Overall risk: {resp.OverallRisk} | Alarms: {resp.Alarms?.Count ?? 0}" }
                : new { id = "risk-alarms", status = "error", detail = "Null response from AI service" });
        }
        catch (Exception ex)
        {
            results.Add(new { id = "risk-alarms", status = "error", detail = ex.Message });
        }

        return new JsonResult(results);
    }
}
