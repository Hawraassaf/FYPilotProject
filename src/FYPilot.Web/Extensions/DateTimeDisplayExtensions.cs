namespace FYPilot.Web.Extensions;

/// <summary>
/// Centralized human-visible date/time formatting for Razor pages.
/// Formatting only -- callers remain responsible for timezone
/// conversion (e.g. call .ToLocalTime() first for a *Utc-suffixed
/// property before formatting), so this helper can never silently
/// perform a double conversion.
/// </summary>
public static class DateTimeDisplayExtensions
{
    public const string DateTimeFormat = "MMM d, yyyy · HH:mm";
    public const string DateFormat = "MMM d, yyyy";

    public static string ToDisplayDateTime(this DateTime value)
        => value.ToString(DateTimeFormat);

    public static string? ToDisplayDateTime(this DateTime? value)
        => value?.ToString(DateTimeFormat);

    public static string ToDisplayDate(this DateTime value)
        => value.ToString(DateFormat);

    public static string? ToDisplayDate(this DateTime? value)
        => value?.ToString(DateFormat);

    public static string ToDisplayDateTime(this DateTimeOffset value)
        => value.ToString(DateTimeFormat);

    public static string? ToDisplayDateTime(this DateTimeOffset? value)
        => value?.ToString(DateTimeFormat);

    public static string ToDisplayDate(this DateTimeOffset value)
        => value.ToString(DateFormat);

    public static string? ToDisplayDate(this DateTimeOffset? value)
        => value?.ToString(DateFormat);
}
