using System.Net;
using System.Net.Mail;
using Microsoft.Extensions.Options;

namespace FYPilot.Infrastructure.Services;

public class SmtpEmailSender(IOptions<SmtpSettings> options) : IEmailSender
{
    private readonly SmtpSettings settings = options.Value;

    public async Task SendAsync(string to, string subject, string htmlBody)
    {
        using var message = new MailMessage
        {
            From = new MailAddress(settings.FromEmail, settings.FromName),
            Subject = subject,
            Body = htmlBody,
            IsBodyHtml = true
        };

        message.To.Add(to);

        using var client = new SmtpClient(settings.Host, settings.Port)
        {
            EnableSsl = settings.EnableSsl,
            Credentials = new NetworkCredential(settings.UserName, settings.Password)
        };

        await client.SendMailAsync(message);
    }
}