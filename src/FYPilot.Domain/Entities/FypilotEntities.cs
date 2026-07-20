using System.ComponentModel.DataAnnotations;
using System.ComponentModel.DataAnnotations.Schema;

namespace FYPilot.Domain.Entities;

[Table("student_skills")]
public class StudentSkill
{
    [Key] [Column("id")] public int Id { get; set; }
    [Column("user_id")] public int UserId { get; set; }
    [Column("skill_name")] public string SkillName { get; set; } = "";
    [Column("rating")] public int Rating { get; set; } = 1;
    [Column("created_at")] public DateTime CreatedAt { get; set; } = DateTime.UtcNow;
    [ForeignKey(nameof(UserId))] public User? User { get; set; }
}

[Table("project_ideas")]
public class ProjectIdea
{
    [Key] [Column("id")] public int Id { get; set; }
    [Column("user_id")] public int UserId { get; set; }
    [Column("title")] public string Title { get; set; } = "";
    [Column("problem_statement")] public string ProblemStatement { get; set; } = "";
    [Column("target_users")] public string TargetUsers { get; set; } = "";
    [Column("why_useful")] public string WhyUseful { get; set; } = "";
    [Column("lebanese_market_relevance")] public string LebaneseMarketRelevance { get; set; } = "";
    [Column("required_technologies")] public string RequiredTechnologies { get; set; } = "";
    [Column("required_skills")] public string RequiredSkills { get; set; } = "";
    [Column("missing_skills")] public string MissingSkills { get; set; } = "";
    [Column("difficulty_level")] public string DifficultyLevel { get; set; } = "intermediate";
    [Column("innovation_score")] public int InnovationScore { get; set; } = 70;
    [Column("feasibility_score")] public int FeasibilityScore { get; set; } = 70;
    [Column("market_demand_score")] public int MarketDemandScore { get; set; } = 70;
    [Column("expected_duration_weeks")] public int ExpectedDurationWeeks { get; set; } = 16;
    [Column("supervisor_category")] public string SupervisorCategory { get; set; } = "";
    [Column("dataset_needed")] public string DatasetNeeded { get; set; } = "";
    [Column("final_deliverables")] public string FinalDeliverables { get; set; } = "";
    [Column("domain")] public string Domain { get; set; } = "";
    [Column("lebanese_sector")] public string LebanesesSector { get; set; } = "";
    [Column("is_selected")] public bool IsSelected { get; set; } = false;
    [Column("created_at")] public DateTime CreatedAt { get; set; } = DateTime.UtcNow;
    [ForeignKey(nameof(UserId))] public User? User { get; set; }
    public FeasibilityReport? FeasibilityReport { get; set; }
    public Project? Project { get; set; }
}

[Table("feasibility_reports")]
public class FeasibilityReport
{
    [Key] [Column("id")] public int Id { get; set; }
    [Column("idea_id")] public int IdeaId { get; set; }
    [Column("user_id")] public int UserId { get; set; }
    [Column("skill_match_score")] public int SkillMatchScore { get; set; }
    [Column("difficulty_match_score")] public int DifficultyMatchScore { get; set; }
    [Column("timeline_fit_score")] public int TimelineFitScore { get; set; }
    [Column("market_usefulness_score")] public int MarketUsefulnessScore { get; set; }
    [Column("innovation_score")] public int InnovationScore { get; set; }
    [Column("risk_score")] public int RiskScore { get; set; }
    [Column("final_feasibility_score")] public int FinalFeasibilityScore { get; set; }
    [Column("explanation")] public string Explanation { get; set; } = "";
    [Column("risks_json")] public string RisksJson { get; set; } = "[]";
    [Column("created_at")] public DateTime CreatedAt { get; set; } = DateTime.UtcNow;
    [ForeignKey(nameof(IdeaId))] public ProjectIdea? Idea { get; set; }
}

[Table("project_roadmaps")]
public class ProjectRoadmap
{
    [Key] [Column("id")] public int Id { get; set; }
    [Column("idea_id")] public int IdeaId { get; set; }
    [Column("user_id")] public int UserId { get; set; }
    [Column("created_at")] public DateTime CreatedAt { get; set; } = DateTime.UtcNow;
    [ForeignKey(nameof(IdeaId))] public ProjectIdea? Idea { get; set; }
    public ICollection<RoadmapPhase> Phases { get; set; } = [];
}

[Table("roadmap_phases")]
public class RoadmapPhase
{
    [Key] [Column("id")] public int Id { get; set; }
    [Column("roadmap_id")] public int RoadmapId { get; set; }
    [Column("phase_number")] public int PhaseNumber { get; set; }
    [Column("name")] public string Name { get; set; } = "";
    [Column("objective")] public string Objective { get; set; } = "";
    [Column("tasks_json")] public string TasksJson { get; set; } = "[]";
    [Column("expected_output")] public string ExpectedOutput { get; set; } = "";
    [Column("tools_needed")] public string ToolsNeeded { get; set; } = "";
    [Column("estimated_weeks")] public int EstimatedWeeks { get; set; } = 1;
    [Column("dependencies")] public string Dependencies { get; set; } = "";
    [Column("risks")] public string Risks { get; set; } = "";
    [Column("success_criteria")] public string SuccessCriteria { get; set; } = "";
    [Column("is_completed")] public bool IsCompleted { get; set; } = false;
    [ForeignKey(nameof(RoadmapId))] public ProjectRoadmap? Roadmap { get; set; }
}

[Table("chat_messages")]
public class ChatMessage
{
    [Key][Column("id")] public int Id { get; set; }
    [Column("user_id")] public int UserId { get; set; }
    [Column("idea_id")] public int? IdeaId { get; set; }
    [Column("mentor_chat_session_id")] public int? MentorChatSessionId { get; set; }
    [Column("role")] public string Role { get; set; } = "user";
    [Column("content")] public string Content { get; set; } = "";
    [Column("created_at")] public DateTime CreatedAt { get; set; } = DateTime.UtcNow;
    [ForeignKey(nameof(UserId))] public User? User { get; set; }
    [ForeignKey(nameof(MentorChatSessionId))] public MentorChatSession? MentorChatSession { get; set; }
}

[Table("market_needs")]
public class MarketNeed
{
    [Key] [Column("id")] public int Id { get; set; }
    [Column("sector")] public string Sector { get; set; } = "";
    [Column("problem")] public string Problem { get; set; } = "";
    [Column("possible_solution")] public string PossibleSolution { get; set; } = "";
    [Column("business_value")] public string BusinessValue { get; set; } = "";
    [Column("demand_score")] public int DemandScore { get; set; } = 70;
    [Column("created_at")] public DateTime CreatedAt { get; set; } = DateTime.UtcNow;
}

[Table("previous_projects")]
public class PreviousProject
{
    [Key] [Column("id")] public int Id { get; set; }
    [Column("title")] public string Title { get; set; } = "";
    [Column("description")] public string Description { get; set; } = "";
    [Column("domain")] public string Domain { get; set; } = "";
    [Column("technologies")] public string Technologies { get; set; } = "";
    [Column("year")] public int Year { get; set; } = 2020;
    [Column("university")] public string University { get; set; } = "";
    [Column("keywords")] public string Keywords { get; set; } = "";
    [Column("summary")] public string Summary { get; set; } = "";
    [Column("created_at")] public DateTime CreatedAt { get; set; } = DateTime.UtcNow;
}

[Table("supervisor_evaluations")]
public class SupervisorEvaluation
{
    [Key] [Column("id")] public int Id { get; set; }
    [Column("idea_id")] public int IdeaId { get; set; }
    [Column("supervisor_id")] public int SupervisorId { get; set; }
    [Column("status")] public string Status { get; set; } = "pending";
    [Column("comment")] public string Comment { get; set; } = "";
    [Column("improvement_suggestions")] public string ImprovementSuggestions { get; set; } = "";
    [Column("similarity_score")] public int SimilarityScore { get; set; } = 0;
    [Column("originality_score")] public int OriginalityScore { get; set; } = 100;
    [Column("created_at")] public DateTime CreatedAt { get; set; } = DateTime.UtcNow;
    [Column("updated_at")] public DateTime UpdatedAt { get; set; } = DateTime.UtcNow;
    [ForeignKey(nameof(IdeaId))] public ProjectIdea? Idea { get; set; }
    [ForeignKey(nameof(SupervisorId))] public User? Supervisor { get; set; }
}
