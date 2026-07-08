namespace FYPilot.Domain.Entities;

public class FeedbackMessage
{
    public int Id { get; set; }

    public int EvaluationId { get; set; }

    public int SenderUserId { get; set; }

    public string MessageText { get; set; } = string.Empty;

    public DateTime CreatedAt { get; set; } = DateTime.UtcNow;

    public DateTime? SeenAt { get; set; }

    public DateTime? EditedAt { get; set; }

    public DateTime? DeletedAt { get; set; }

    public int? ReplyToMessageId { get; set; }

    public SupervisorEvaluation? Evaluation { get; set; }
}
