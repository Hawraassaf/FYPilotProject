namespace FYPilot.Web.Configuration;

public class GoogleCalendarSettings
{
    public string ClientId { get; set; } = "";
    public string ClientSecret { get; set; } = "";
    public string RedirectUri { get; set; } =
        "http://localhost:8080/Supervisor/GoogleCalendarCallback";
}