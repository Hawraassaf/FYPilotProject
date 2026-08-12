using System.Net;
using System.Net.Mail;
using Microsoft.Extensions.Logging;
using Microsoft.Extensions.Options;

namespace FYPilot.Infrastructure.Services;

public class SmtpEmailSender(
    IOptions<SmtpSettings> options,
    ILogger<SmtpEmailSender> logger) : IEmailSender
{
    private readonly SmtpSettings settings = options.Value;

    public async Task SendAsync(string to, string subject, string htmlBody)
    {
        // TEMPORARY DIAGNOSTIC LOGGING — collaborator invitation
        // email investigation. Never logs the password itself.
        logger.LogInformation(
            "SMTP send attempt. Host={Host} Port={Port} " +
            "UsernameConfigured={UsernameConfigured} " +
            "PasswordConfigured={PasswordConfigured} " +
            "PasswordLength={PasswordLength} " +
            "FromEmail={FromEmail} EnableSsl={EnableSsl} " +
            "Recipient={Recipient}",
            settings.Host,
            settings.Port,
            !string.IsNullOrWhiteSpace(settings.UserName),
            !string.IsNullOrWhiteSpace(settings.Password),
            settings.Password.Length,
            settings.FromEmail,
            settings.EnableSsl,
            to);

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

        try
        {
            await client.SendMailAsync(message);

            logger.LogInformation(
                "SMTP send succeeded for recipient {Recipient}.",
                to);
        }
        catch (Exception ex)
        {
            var inner = ex.InnerException;
            var innerType = inner?.GetType().FullName;
            var innerMessage = inner?.Message;

            logger.LogError(
                ex,
                "SMTP send failed for recipient {Recipient}. " +
                "ExceptionType={ExceptionType} " +
                "InnerExceptionType={InnerExceptionType} " +
                "InnerExceptionMessage={InnerExceptionMessage}",
                to,
                ex.GetType().FullName,
                innerType,
                innerMessage);

            throw;
        }
    }
}