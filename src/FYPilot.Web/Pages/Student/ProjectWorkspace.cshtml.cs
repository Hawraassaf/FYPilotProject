using System.Security.Claims;
using FYPilot.Application.Interfaces;
using FYPilot.Domain.Entities;
using FYPilot.Infrastructure.Data;
using FYPilot.Web.Hubs;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;
using Microsoft.AspNetCore.Mvc.RazorPages;
using Microsoft.AspNetCore.SignalR;
using Microsoft.EntityFrameworkCore;

namespace FYPilot.Web.Pages.Student;

[Authorize(Roles = "student")]
public class ProjectWorkspaceModel(
    ApplicationDbContext db,
    IProjectAccessService projectAccessService,
    IActiveProjectService activeProjectService,
    IHubContext<ProjectDiscussionHub> hubContext,
    IWebHostEnvironment environment,
    ILogger<ProjectWorkspaceModel> logger)
    : PageModel
{
    private const int InitialMessageLimit = 100;
    private const int MaximumMessageLength = 1000;
    private const int MaximumAttachmentsPerMessage = 5;
    private const long MaximumAttachmentBytes = 10 * 1024 * 1024;

    private static readonly IReadOnlyDictionary<string, string>
        AllowedFileTypes =
            new Dictionary<string, string>(
                StringComparer.OrdinalIgnoreCase)
            {
                [".pdf"] = "application/pdf",
                [".doc"] = "application/msword",
                [".docx"] =
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                [".xls"] = "application/vnd.ms-excel",
                [".xlsx"] =
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                [".csv"] = "text/csv",
                [".txt"] = "text/plain",
                [".png"] = "image/png",
                [".jpg"] = "image/jpeg",
                [".jpeg"] = "image/jpeg",
                [".webp"] = "image/webp",
                [".zip"] = "application/zip"
            };

    [BindProperty(SupportsGet = true)]
    public int ProjectId { get; set; }

    public ProjectAccessResult? ProjectAccess
    {
        get;
        private set;
    }

    public int CurrentUserId { get; private set; }

    public bool IsOwner =>
        ProjectAccess?.IsOwner == true;

    public string ProjectTitle { get; private set; } =
        "Untitled Project";

    public string ProjectStatus { get; private set; } =
        "Active";

    public string SelectedIdeaTitle { get; private set; } =
        "No official idea selected";

    public int ActiveMembersCount { get; private set; }

    public int MaximumMembers { get; private set; } = 1;

    public List<MemberItem> Members
    {
        get;
        private set;
    } = [];

    public List<MessageItem> Messages
    {
        get;
        private set;
    } = [];

    public string? ErrorMessage { get; private set; }

    public sealed record MemberItem(
        int UserId,
        string FullName,
        string Initials,
        string Role);

    public sealed record AttachmentItem(
        int Id,
        string OriginalFileName,
        string ContentType,
        long SizeBytes,
        string DownloadUrl,
        bool IsImage);

    public sealed record ReplyItem(
        int Id,
        string FullName,
        string Content,
        bool IsDeleted);

    public sealed record MessageItem(
        int Id,
        int UserId,
        string FullName,
        string Initials,
        string Content,
        DateTime CreatedAtUtc,
        bool IsEdited,
        bool IsDeleted,
        bool CanEdit,
        bool CanDelete,
        ReplyItem? ReplyTo,
        List<AttachmentItem> Attachments);

    public async Task<IActionResult> OnGetAsync(
        CancellationToken cancellationToken)
    {
        var userId =
            GetCurrentUserId();

        if (userId == null)
        {
            return RedirectToPage(
                "/Account/Login");
        }

        CurrentUserId =
            userId.Value;

        

        if (!await LoadWorkspaceAsync(
                userId.Value,
                cancellationToken))
        {
            TempData["Error"] =
                "You do not have access to that project.";

            return RedirectToPage(
                "/Student/MyProjects");
        }

        await activeProjectService.RememberPageAsync(
            userId.Value,
            ProjectId,
            "/Student/ProjectWorkspace",
            cancellationToken);

        return Page();
    }

    public async Task<IActionResult>
        OnPostSendMessageAsync(
            int projectId,
            string? content,
            int? replyToMessageId,
            List<IFormFile>? files,
            CancellationToken cancellationToken)
    {
        ProjectId =
            projectId;

        var userId =
            GetCurrentUserId();

        if (userId == null)
        {
            return new JsonResult(
                Failure(
                    "Authentication is required."))
            {
                StatusCode =
                    StatusCodes.Status401Unauthorized
            };
        }

        var access =
            await projectAccessService.GetAccessAsync(
                ProjectId,
                userId.Value,
                "student",
                cancellationToken);

        if (access == null)
        {
            return new JsonResult(
                Failure(
                    "You no longer have access to this project."))
            {
                StatusCode =
                    StatusCodes.Status403Forbidden
            };
        }

        var cleanContent =
            content?.Trim() ?? string.Empty;

        var uploadedFiles =
            files?
                .Where(file =>
                    file != null &&
                    file.Length > 0)
                .ToList()
            ?? [];

        if (string.IsNullOrWhiteSpace(
                cleanContent) &&
            uploadedFiles.Count == 0)
        {
            return BadRequest(
                Failure(
                    "Write a message or attach a file."));
        }

        if (cleanContent.Length >
            MaximumMessageLength)
        {
            return BadRequest(
                Failure(
                    $"Messages cannot exceed "
                    + $"{MaximumMessageLength} characters."));
        }

        if (uploadedFiles.Count >
            MaximumAttachmentsPerMessage)
        {
            return BadRequest(
                Failure(
                    $"A message can contain at most "
                    + $"{MaximumAttachmentsPerMessage} files."));
        }

        ProjectDiscussionMessage? replyMessage =
            null;

        if (replyToMessageId.HasValue)
        {
            replyMessage =
                await db.ProjectDiscussionMessages
                    .AsNoTracking()
                    .Include(message =>
                        message.User)
                    .FirstOrDefaultAsync(
                        message =>
                            message.Id ==
                                replyToMessageId.Value &&
                            message.ProjectId ==
                                ProjectId,
                        cancellationToken);

            if (replyMessage == null ||
                replyMessage.IsDeleted)
            {
                return BadRequest(
                    Failure(
                        "The message being replied to "
                        + "is unavailable."));
            }
        }

        foreach (var file in uploadedFiles)
        {
            var validationError =
                await ValidateFileAsync(
                    file,
                    cancellationToken);

            if (validationError != null)
            {
                return BadRequest(
                    Failure(
                        validationError));
            }
        }

        var user =
            await db.Users
                .AsNoTracking()
                .Where(item =>
                    item.Id == userId.Value)
                .Select(item => new
                {
                    item.FullName
                })
                .FirstOrDefaultAsync(
                    cancellationToken);

        if (user == null)
        {
            return BadRequest(
                Failure(
                    "The student account could not be found."));
        }

        var now =
            DateTime.UtcNow;

        var storedFullPaths =
            new List<string>();

        await using var transaction =
            await db.Database.BeginTransactionAsync(
                cancellationToken);

        try
        {
            var message =
                new ProjectDiscussionMessage
                {
                    ProjectId =
                        ProjectId,

                    UserId =
                        userId.Value,

                    Content =
                        cleanContent,

                    ReplyToMessageId =
                        replyToMessageId,

                    IsEdited =
                        false,

                    IsDeleted =
                        false,

                    CreatedAtUtc =
                        now
                };

            db.ProjectDiscussionMessages.Add(
                message);

            await db.SaveChangesAsync(
                cancellationToken);

            var attachments =
                new List<ProjectDiscussionAttachment>();

            if (uploadedFiles.Count > 0)
            {
                var storageDirectory =
                    Path.Combine(
                        environment.ContentRootPath,
                        "App_Data",
                        "ProjectDiscussion",
                        ProjectId.ToString());

                Directory.CreateDirectory(
                    storageDirectory);

                foreach (var file in uploadedFiles)
                {
                    var extension =
                        Path.GetExtension(
                            file.FileName)
                            .ToLowerInvariant();

                    var storedFileName =
                        $"{Guid.NewGuid():N}{extension}";

                    var fullPath =
                        Path.Combine(
                            storageDirectory,
                            storedFileName);

                    await using (
                        var output =
                            System.IO.File.Create(
                                fullPath))
                    {
                        await file.CopyToAsync(
                            output,
                            cancellationToken);
                    }

                    storedFullPaths.Add(
                        fullPath);

                    var relativePath =
                        Path.GetRelativePath(
                            environment.ContentRootPath,
                            fullPath)
                            .Replace(
                                '\\',
                                '/');

                    attachments.Add(
                        new ProjectDiscussionAttachment
                        {
                            MessageId =
                                message.Id,

                            OriginalFileName =
                                SafeOriginalFileName(
                                    file.FileName),

                            StoredFileName =
                                storedFileName,

                            ContentType =
                                AllowedFileTypes[
                                    extension],

                            SizeBytes =
                                file.Length,

                            RelativePath =
                                relativePath,

                            UploadedAtUtc =
                                now
                        });
                }

                db.ProjectDiscussionAttachments.AddRange(
                    attachments);

                await db.SaveChangesAsync(
                    cancellationToken);
            }

            await transaction.CommitAsync(
                cancellationToken);

            var attachmentPayload =
                attachments
                    .Select(attachment =>
                        new
                        {
                            id =
                                attachment.Id,

                            originalFileName =
                                attachment.OriginalFileName,

                            contentType =
                                attachment.ContentType,

                            sizeBytes =
                                attachment.SizeBytes,

                            downloadUrl =
                                Url.Page(
                                    "/Student/ProjectWorkspace",
                                    "Attachment",
                                    new
                                    {
                                        projectId =
                                            ProjectId,

                                        attachmentId =
                                            attachment.Id
                                    }),

                            isImage =
                                attachment.ContentType
                                    .StartsWith(
                                        "image/",
                                        StringComparison
                                            .OrdinalIgnoreCase)
                        })
                    .ToList();

            var payload =
                new
                {
                    id =
                        message.Id,

                    projectId =
                        ProjectId,

                    userId =
                        userId.Value,

                    fullName =
                        SafeName(
                            user.FullName),

                    initials =
                        Initials(
                            user.FullName),

                    content =
                        message.Content,

                    createdAtUtc =
                        message.CreatedAtUtc,

                    createdAtDisplay =
                        message.CreatedAtUtc
                            .ToLocalTime()
                            .ToString(
                                "MMM d, yyyy h:mm tt"),

                    isEdited =
                        false,

                    isDeleted =
                        false,

                    replyTo = replyMessage == null
                        ? null
                        : new
                        {
                            id =
                                replyMessage.Id,

                            fullName =
                                SafeName(
                                    replyMessage.User
                                        ?.FullName),

                            content =
                                ShortText(
                                    replyMessage.Content,
                                    120)
                        },

                    attachments =
                        attachmentPayload
                };

            var recipientUserIds =
                await db.ProjectMembers
                    .AsNoTracking()
                    .Where(member =>
                        member.ProjectId ==
                            ProjectId &&
                        member.Status ==
                            "active")
                    .Select(member =>
                        member.UserId.ToString())
                    .Distinct()
                    .ToListAsync(
                        cancellationToken);

            if (recipientUserIds.Count > 0)
            {
                await hubContext.Clients
                    .Users(
                        recipientUserIds)
                    .SendAsync(
                        "MessageCreated",
                        payload,
                        cancellationToken);
            }

            await activeProjectService
                .RememberPageAsync(
                    userId.Value,
                    ProjectId,
                    "/Student/ProjectWorkspace",
                    cancellationToken);

            return new JsonResult(
                new
                {
                    success = true,
                    message = payload
                });
        }
        catch (Exception exception)
        {
            try
            {
                await transaction.RollbackAsync(
                    cancellationToken);
            }
            catch
            {
                // Preserve the original error.
            }

            foreach (var fullPath in storedFullPaths)
            {
                try
                {
                    if (System.IO.File.Exists(
                            fullPath))
                    {
                        System.IO.File.Delete(
                            fullPath);
                    }
                }
                catch
                {
                    // Cleanup failure is logged with
                    // the original operation below.
                }
            }

            logger.LogError(
                exception,
                "Failed to send project discussion "
                + "message for project {ProjectId} "
                + "by user {UserId}.",
                ProjectId,
                userId.Value);

            return new JsonResult(
                Failure(
                    "The message could not be sent."))
            {
                StatusCode =
                    StatusCodes
                        .Status500InternalServerError
            };
        }
    }

    public async Task<IActionResult>
        OnGetAttachmentAsync(
            int projectId,
            int attachmentId,
            CancellationToken cancellationToken)
    {
        ProjectId =
            projectId;

        var userId =
            GetCurrentUserId();

        if (userId == null)
        {
            return Unauthorized();
        }

        var access =
            await projectAccessService.GetAccessAsync(
                ProjectId,
                userId.Value,
                "student",
                cancellationToken);

        if (access == null)
        {
            return Forbid();
        }

        var attachment =
            await db.ProjectDiscussionAttachments
                .AsNoTracking()
                .Include(item =>
                    item.Message)
                .FirstOrDefaultAsync(
                    item =>
                        item.Id ==
                            attachmentId &&
                        item.Message != null &&
                        item.Message.ProjectId ==
                            ProjectId,
                    cancellationToken);

        if (attachment == null)
        {
            return NotFound();
        }

        var storageRoot =
            Path.GetFullPath(
                Path.Combine(
                    environment.ContentRootPath,
                    "App_Data",
                    "ProjectDiscussion"));

        var fullPath =
            Path.GetFullPath(
                Path.Combine(
                    environment.ContentRootPath,
                    attachment.RelativePath
                        .Replace(
                            '/',
                            Path.DirectorySeparatorChar)));

        if (!fullPath.StartsWith(
                storageRoot +
                Path.DirectorySeparatorChar,
                StringComparison.OrdinalIgnoreCase))
        {
            logger.LogWarning(
                "Blocked invalid discussion attachment "
                + "path for attachment {AttachmentId}.",
                attachmentId);

            return NotFound();
        }

        if (!System.IO.File.Exists(
                fullPath))
        {
            return NotFound();
        }

        return new PhysicalFileResult(
    fullPath,
    attachment.ContentType)
        {
            FileDownloadName =
        attachment.OriginalFileName,

            EnableRangeProcessing =
        true
        };
    }

    private async Task<bool> LoadWorkspaceAsync(
        int userId,
        CancellationToken cancellationToken)
    {
        if (ProjectId <= 0)
        {
            return false;
        }

        ProjectAccess =
            await projectAccessService.GetAccessAsync(
                ProjectId,
                userId,
                "student",
                cancellationToken);

        if (ProjectAccess == null)
        {
            return false;
        }

        var project =
            await db.Projects
                .AsNoTracking()
                .Include(item =>
                    item.ProjectIdea)
                .Include(item =>
                    item.Members
                        .Where(member =>
                            member.Status ==
                                "active"))
                .ThenInclude(member =>
                    member.User)
                .AsSplitQuery()
                .FirstOrDefaultAsync(
                    item =>
                        item.Id == ProjectId,
                    cancellationToken);

        if (project == null)
        {
            return false;
        }

        ProjectTitle =
            string.IsNullOrWhiteSpace(
                project.Title)
                ? "Untitled Project"
                : project.Title.Trim();

        ProjectStatus =
            ToDisplayText(
                project.Status);

        SelectedIdeaTitle =
            project.ProjectIdea == null
                ? "No official idea selected"
                : project.ProjectIdea.Title;

        MaximumMembers =
            Math.Clamp(
                project.MaximumMembers,
                1,
                3);

        Members =
            project.Members
                .Where(member =>
                    member.Status ==
                        "active" &&
                    member.User != null)
                .OrderBy(member =>
                    Normalize(
                        member.Role) ==
                    "owner"
                        ? 0
                        : 1)
                .ThenBy(member =>
                    member.JoinedAt)
                .Select(member =>
                    new MemberItem(
                        member.UserId,
                        SafeName(
                            member.User!.FullName),
                        Initials(
                            member.User.FullName),
                        ToDisplayText(
                            member.Role)))
                .ToList();

        ActiveMembersCount =
            Members.Count;

        var latestMessages =
            await db.ProjectDiscussionMessages
                .AsNoTracking()
                .Include(message =>
                    message.User)
                .Include(message =>
                    message.ReplyToMessage)
                .ThenInclude(reply =>
                    reply!.User)
                .Include(message =>
                    message.Attachments)
                .Where(message =>
                    message.ProjectId ==
                        ProjectId)
                .OrderByDescending(message =>
                    message.Id)
                .Take(
                    InitialMessageLimit)
                .ToListAsync(
                    cancellationToken);

        Messages =
            latestMessages
                .OrderBy(message =>
                    message.Id)
                .Select(message =>
                {
                    ReplyItem? reply =
                        null;

                    if (message.ReplyToMessage != null)
                    {
                        reply =
                            new ReplyItem(
                                message.ReplyToMessage.Id,
                                SafeName(
                                    message.ReplyToMessage
                                        .User?.FullName),
                                message.ReplyToMessage
                                    .IsDeleted
                                    ? "This message was deleted."
                                    : ShortText(
                                        message.ReplyToMessage
                                            .Content,
                                        120),
                                message.ReplyToMessage
                                    .IsDeleted);
                    }

                    var attachments =
                        message.Attachments
                            .OrderBy(item =>
                                item.Id)
                            .Select(item =>
                                new AttachmentItem(
                                    item.Id,
                                    item.OriginalFileName,
                                    item.ContentType,
                                    item.SizeBytes,
                                    Url.Page(
                                        "/Student/ProjectWorkspace",
                                        "Attachment",
                                        new
                                        {
                                            projectId =
                                                ProjectId,

                                            attachmentId =
                                                item.Id
                                        })
                                    ?? "#",
                                    item.ContentType
                                        .StartsWith(
                                            "image/",
                                            StringComparison
                                                .OrdinalIgnoreCase)))
                            .ToList();

                    return new MessageItem(
                        message.Id,
                        message.UserId,
                        SafeName(
                            message.User?.FullName),
                        Initials(
                            message.User?.FullName),
                        message.IsDeleted
                            ? string.Empty
                            : message.Content,
                        message.CreatedAtUtc,
                        message.IsEdited,
                        message.IsDeleted,
                        !message.IsDeleted &&
                        message.UserId ==
                            userId,
                        !message.IsDeleted &&
                        (
                            message.UserId ==
                                userId ||
                            IsOwner
                        ),
                        reply,
                        attachments);
                })
                .ToList();

        return true;
    }

    private static async Task<string?>
        ValidateFileAsync(
            IFormFile file,
            CancellationToken cancellationToken)
    {
        if (file.Length <= 0)
        {
            return "Empty files cannot be uploaded.";
        }

        if (file.Length >
            MaximumAttachmentBytes)
        {
            return $"{Path.GetFileName(file.FileName)} "
                   + "is larger than 10 MB.";
        }

        var extension =
            Path.GetExtension(
                file.FileName)
                .ToLowerInvariant();

        if (!AllowedFileTypes.ContainsKey(
                extension))
        {
            return $"{Path.GetFileName(file.FileName)} "
                   + "uses an unsupported file type.";
        }

        if (!await HasValidSignatureAsync(
                file,
                extension,
                cancellationToken))
        {
            return $"{Path.GetFileName(file.FileName)} "
                   + "does not match its file extension.";
        }

        return null;
    }

    private static async Task<bool>
        HasValidSignatureAsync(
            IFormFile file,
            string extension,
            CancellationToken cancellationToken)
    {
        await using var stream =
            file.OpenReadStream();

        var header =
            new byte[16];

        var bytesRead =
            await stream.ReadAsync(
                header.AsMemory(
                    0,
                    header.Length),
                cancellationToken);

        bool StartsWith(
            params byte[] signature)
        {
            return bytesRead >=
                   signature.Length &&
                   header
                       .Take(
                           signature.Length)
                       .SequenceEqual(
                           signature);
        }

        return extension switch
        {
            ".pdf" =>
                StartsWith(
                    0x25,
                    0x50,
                    0x44,
                    0x46),

            ".png" =>
                StartsWith(
                    0x89,
                    0x50,
                    0x4E,
                    0x47,
                    0x0D,
                    0x0A,
                    0x1A,
                    0x0A),

            ".jpg" or ".jpeg" =>
                StartsWith(
                    0xFF,
                    0xD8,
                    0xFF),

            ".webp" =>
                bytesRead >= 12 &&
                header[0] == 0x52 &&
                header[1] == 0x49 &&
                header[2] == 0x46 &&
                header[3] == 0x46 &&
                header[8] == 0x57 &&
                header[9] == 0x45 &&
                header[10] == 0x42 &&
                header[11] == 0x50,

            ".docx" or ".xlsx" or ".zip" =>
                StartsWith(
                    0x50,
                    0x4B),

            ".doc" or ".xls" =>
                StartsWith(
                    0xD0,
                    0xCF,
                    0x11,
                    0xE0,
                    0xA1,
                    0xB1,
                    0x1A,
                    0xE1),

            ".txt" or ".csv" =>
                !header
                    .Take(
                        bytesRead)
                    .Contains(
                        (byte)0x00),

            _ =>
                false
        };
    }

    private int? GetCurrentUserId()
    {
        var value =
            User.FindFirst(
                ClaimTypes.NameIdentifier)
                ?.Value;

        return int.TryParse(
            value,
            out var userId)
                ? userId
                : null;
    }

    private static object Failure(
        string message)
    {
        return new
        {
            success = false,
            message
        };
    }

    private static string SafeOriginalFileName(
        string? fileName)
    {
        var safe =
            Path.GetFileName(
                fileName ?? "file");

        if (safe.Length <= 255)
        {
            return safe;
        }

        var extension =
            Path.GetExtension(
                safe);

        var baseName =
            Path.GetFileNameWithoutExtension(
                safe);

        var maximumBaseLength =
            Math.Max(
                1,
                255 - extension.Length);

        return baseName[
                ..Math.Min(
                    baseName.Length,
                    maximumBaseLength)]
            + extension;
    }

    private static string SafeName(
        string? value)
    {
        return string.IsNullOrWhiteSpace(
            value)
                ? "Student"
                : value.Trim();
    }

    private static string Initials(
        string? fullName)
    {
        if (string.IsNullOrWhiteSpace(
                fullName))
        {
            return "ST";
        }

        var parts =
            fullName
                .Split(
                    ' ',
                    StringSplitOptions
                        .RemoveEmptyEntries)
                .Take(2)
                .ToList();

        return parts.Count == 0
            ? "ST"
            : string.Join(
                    "",
                    parts.Select(
                        part =>
                            part[0]))
                .ToUpperInvariant();
    }

    private static string ShortText(
        string? content,
        int maximumLength)
    {
        var clean =
            content?.Trim() ?? string.Empty;

        return clean.Length <=
               maximumLength
            ? clean
            : clean[..maximumLength] +
              "…";
    }

    private static string Normalize(
        string? value)
    {
        return string.IsNullOrWhiteSpace(
            value)
                ? string.Empty
                : value.Trim()
                    .ToLowerInvariant();
    }

    private static string ToDisplayText(
        string? value)
    {
        if (string.IsNullOrWhiteSpace(
                value))
        {
            return "Active";
        }

        var clean =
            value.Trim()
                .Replace(
                    "_",
                    " ")
                .Replace(
                    "-",
                    " ");

        return string.Join(
            " ",
            clean.Split(
                    ' ',
                    StringSplitOptions
                        .RemoveEmptyEntries)
                .Select(word =>
                    char.ToUpperInvariant(
                        word[0]) +
                    word[1..]
                        .ToLowerInvariant()));
    }
}