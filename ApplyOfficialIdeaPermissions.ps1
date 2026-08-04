$ErrorActionPreference = "Stop"

$repoRoot = (Get-Location).Path

$ideaGeneratorBackend = Join-Path $repoRoot "src\FYPilot.Web\Pages\Student\IdeaGenerator.cshtml.cs"
$ideaComparisonBackend = Join-Path $repoRoot "src\FYPilot.Web\Pages\Student\IdeaComparison.cshtml.cs"
$ideaGeneratorView = Join-Path $repoRoot "src\FYPilot.Web\Pages\Student\IdeaGenerator.cshtml"

function Replace-ExactlyOnce {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Content,

        [Parameter(Mandatory = $true)]
        [string]$OldText,

        [Parameter(Mandatory = $true)]
        [string]$NewText,

        [Parameter(Mandatory = $true)]
        [string]$Label
    )

    $firstIndex = $Content.IndexOf(
        $OldText,
        [System.StringComparison]::Ordinal)

    if ($firstIndex -lt 0) {
        throw "Could not find the expected code for: $Label."
    }

    $secondIndex = $Content.IndexOf(
        $OldText,
        $firstIndex + $OldText.Length,
        [System.StringComparison]::Ordinal)

    if ($secondIndex -ge 0) {
        throw "Found the expected code more than once for: $Label."
    }

    return [string]::Concat(
        $Content.Substring(0, $firstIndex),
        $NewText,
        $Content.Substring($firstIndex + $OldText.Length))
}

function Read-CodeFile {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path
    )

    if (-not (Test-Path -LiteralPath $Path)) {
        throw "File not found: $Path"
    }

    $original = [System.IO.File]::ReadAllText($Path)

    return [pscustomobject]@{
        Path = $Path
        Original = $original
        UsedCrLf = $original.Contains("`r`n")
        Normalized = $original.Replace("`r`n", "`n")
    }
}

function Write-CodeFile {
    param(
        [Parameter(Mandatory = $true)]
        [pscustomobject]$File,

        [Parameter(Mandatory = $true)]
        [string]$UpdatedNormalized
    )

    $backupPath =
        "$($File.Path).before-official-idea-permission.bak"

    [System.IO.File]::Copy(
        $File.Path,
        $backupPath,
        $true)

    $output = $UpdatedNormalized

    if ($File.UsedCrLf) {
        $output = $output.Replace("`n", "`r`n")
    }

    $utf8WithoutBom =
        New-Object System.Text.UTF8Encoding($false)

    [System.IO.File]::WriteAllText(
        $File.Path,
        $output,
        $utf8WithoutBom)

    Write-Host "Updated: $($File.Path)"
    Write-Host "Backup:  $backupPath"
}

$generatorBackendFile =
    Read-CodeFile -Path $ideaGeneratorBackend

$comparisonBackendFile =
    Read-CodeFile -Path $ideaComparisonBackend

$generatorViewFile =
    Read-CodeFile -Path $ideaGeneratorView

$generatorBackendUpdated =
    $generatorBackendFile.Normalized

$oldGeneratorPermissionBlock = @'
        /*
         * The official shared project idea is selected
         * by the project owner.
         */
        var access =
            await projectAccessService.GetAccessAsync(
                ProjectId,
                userId,
                "student",
                cancellationToken);

        if (access == null)
        {
            TempData["Error"] =
                "You do not have access to that project.";

            return RedirectToPage(
                "/Student/MyProjects");
        }

        if (!access.IsOwner)
        {
            TempData["Error"] =
                "Only the project owner can select "
                + "the official project idea.";

            return RedirectToGenerator(ideaId);
        }
'@

$newGeneratorPermissionBlock = @'
        /*
         * Any active project member may select or replace
         * the official shared project idea.
         */
        var access =
            await projectAccessService.GetAccessAsync(
                ProjectId,
                userId,
                "student",
                cancellationToken);

        if (access?.CanView != true)
        {
            TempData["Error"] =
                "You do not have access to that project.";

            return RedirectToPage(
                "/Student/MyProjects");
        }

        if (!access.CanEdit)
        {
            TempData["Error"] =
                "Restore this project before selecting "
                + "the official project idea.";

            return RedirectToGenerator(ideaId);
        }
'@

$generatorBackendUpdated = Replace-ExactlyOnce `
    -Content $generatorBackendUpdated `
    -OldText $oldGeneratorPermissionBlock `
    -NewText $newGeneratorPermissionBlock `
    -Label "Idea Generator permission check"

$oldGeneratorIdeaScope = @'
         item =>
             item.Id == ideaId &&
             item.UserId == userId &&
             item.GeneratedForProjectId ==
                 ProjectId,
'@

$newGeneratorIdeaScope = @'
         item =>
             item.Id == ideaId &&
             item.GeneratedForProjectId ==
                 ProjectId,
'@

$generatorBackendUpdated = Replace-ExactlyOnce `
    -Content $generatorBackendUpdated `
    -OldText $oldGeneratorIdeaScope `
    -NewText $newGeneratorIdeaScope `
    -Label "Idea Generator project candidate scope"

$oldGeneratorIdeaError = @'
                TempData["Error"] =
    "The selected idea was not found in "
    + "this project or does not belong "
    + "to your account.";
'@

$newGeneratorIdeaError = @'
                TempData["Error"] =
                    "The selected idea was not found "
                    + "inside this project.";
'@

$generatorBackendUpdated = Replace-ExactlyOnce `
    -Content $generatorBackendUpdated `
    -OldText $oldGeneratorIdeaError `
    -NewText $newGeneratorIdeaError `
    -Label "Idea Generator candidate error message"

$generatorBackendUpdated = Replace-ExactlyOnce `
    -Content $generatorBackendUpdated `
    -OldText '                ?? "The project owner";' `
    -NewText '                ?? "A project member";' `
    -Label "Idea Generator activity actor fallback"

$comparisonBackendUpdated =
    $comparisonBackendFile.Normalized

$oldComparisonPermissionBlock = @'
        if (ProjectAccess?.IsOwner != true)
        {
            TempData["Error"] =
                "Only the project owner can select "
                + "the official project idea.";

            return RedirectToPage(
                new
                {
                    projectId = ProjectId
                });
        }
'@

$newComparisonPermissionBlock = @'
        if (ProjectAccess?.CanEdit != true)
        {
            TempData["Error"] =
                "Restore this project before selecting "
                + "the official project idea.";

            return RedirectToPage(
                new
                {
                    projectId = ProjectId
                });
        }
'@

$comparisonBackendUpdated = Replace-ExactlyOnce `
    -Content $comparisonBackendUpdated `
    -OldText $oldComparisonPermissionBlock `
    -NewText $newComparisonPermissionBlock `
    -Label "Idea Comparison permission check"

$comparisonBackendUpdated = Replace-ExactlyOnce `
    -Content $comparisonBackendUpdated `
    -OldText '                ?? "The project owner";' `
    -NewText '                ?? "A project member";' `
    -Label "Idea Comparison activity actor fallback"

$generatorViewUpdated =
    Replace-ExactlyOnce `
        -Content $generatorViewFile.Normalized `
        -OldText 'else if (Model.ProjectAccess?.IsOwner == true)' `
        -NewText 'else if (Model.ProjectAccess?.CanEdit == true)' `
        -Label "Idea Generator Select Idea button visibility"

if ($generatorBackendUpdated -eq
        $generatorBackendFile.Normalized) {
    throw "No Idea Generator backend changes were produced."
}

if ($comparisonBackendUpdated -eq
        $comparisonBackendFile.Normalized) {
    throw "No Idea Comparison backend changes were produced."
}

if ($generatorViewUpdated -eq
        $generatorViewFile.Normalized) {
    throw "No Idea Generator view changes were produced."
}

# Nothing is written until every required match above succeeds.
Write-CodeFile `
    -File $generatorBackendFile `
    -UpdatedNormalized $generatorBackendUpdated

Write-CodeFile `
    -File $comparisonBackendFile `
    -UpdatedNormalized $comparisonBackendUpdated

Write-CodeFile `
    -File $generatorViewFile `
    -UpdatedNormalized $generatorViewUpdated

Write-Host ""
Write-Host "Official idea permissions updated successfully."
Write-Host "Dashboard.cshtml.cs was intentionally not changed."
