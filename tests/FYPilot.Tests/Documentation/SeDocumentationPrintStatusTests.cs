using System.IO;
using FYPilot.Domain.Entities;
using FYPilot.Web.Pages.Student;
using Xunit;

namespace FYPilot.Tests.Documentation;

/// <summary>
/// Root cause this protects against: DocumentationGenerator.cshtml's print
/// CSS hides everything except #se-report (`body * { visibility: hidden }`,
/// `#se-report, #se-report * { visibility: visible }`), and additionally
/// force-hides the on-screen Quality Passport with
/// `.doc-passport { display: none !important; }` inside @media print. A
/// student could export a PDF that looked fully clean/approved even when
/// the on-screen page showed "Rejected", "Unresolved issues", or "Review
/// unavailable". The fix adds a static .doc-print-status summary INSIDE
/// #se-report (so it survives the visibility rule) built from the exact
/// same already-computed review/provenance fields the on-screen passport
/// uses -- see DocumentationGeneratorModel.IsAcceptedForPrint.
///
/// Two test strategies, matching what the repository actually supports:
/// - IsAcceptedForPrint is a pure static method -- tested directly with
///   constructed AiOutputReview/QualityPanelView combinations (Tests 5-9).
/// - The surrounding Razor/CSS structure has no rendering test harness in
///   this repository (no WebApplicationFactory/Razor-runtime-render setup
///   exists for any page), so it is verified via a source-contract test
///   against the actual .cshtml file text (Tests 1-4, 10) -- the strongest
///   pattern actually available here.
/// </summary>
public class SeDocumentationPrintStatusTests
{
    // ------------------------------------------------------------------
    // IsAcceptedForPrint -- dynamic, pure-function tests
    // ------------------------------------------------------------------

    private static DocumentationGeneratorModel.QualityPanelView Quality(bool semanticReviewCompleted) =>
        new(
            OverallScore: 80, ReviewStatusLabel: "label", ReviewStatusCssClass: "bg-secondary",
            OutputReviewLevelLabel: "detail", SemanticReviewCompleted: semanticReviewCompleted,
            ProvenanceSummary: "deepinfra", ImportantWarningCount: 0, LastGeneratedAt: DateTime.UtcNow);

    private static AiOutputReview Review(bool usable, string status) =>
        new() { Usable = usable, Status = status, DecisionReason = "", CreatedAt = DateTime.UtcNow };

    // ---- Test 8: accepted document (both real accepted statuses) ----

    [Theory]
    [InlineData("approved")]
    [InlineData("approved_with_minor_warnings")]
    public void Accepted_ExplicitAcceptedStatus_UsableAndSemanticReviewCompleted_IsAcceptedForPrint(string status)
    {
        var accepted = DocumentationGeneratorModel.IsAcceptedForPrint(
            Review(usable: true, status: status), Quality(semanticReviewCompleted: true));

        Assert.True(accepted);
    }

    // ---- Regression: unresolved has Usable=true AND SemanticReviewCompleted=true,
    // but is NOT an accepted status -- this is the exact contradiction the
    // audit found (accepted headline + "unresolved findings remain" detail
    // row shown together). ----

    [Fact]
    public void Unresolved_UsableTrue_SemanticReviewCompletedTrue_IsStillNotAcceptedForPrint()
    {
        var accepted = DocumentationGeneratorModel.IsAcceptedForPrint(
            Review(usable: true, status: "unresolved"), Quality(semanticReviewCompleted: true));

        Assert.False(accepted);
    }

    // ---- Test 5: review rejected ----

    [Fact]
    public void Rejected_NotUsable_IsNotAcceptedForPrint_EvenThoughSemanticReviewCompleted()
    {
        // "rejected" still technically completes semantic review (it only
        // fails to be *usable*) -- this is exactly why Usable must be
        // checked, not just SemanticReviewCompleted.
        var accepted = DocumentationGeneratorModel.IsAcceptedForPrint(
            Review(usable: false, status: "rejected"), Quality(semanticReviewCompleted: true));

        Assert.False(accepted);
    }

    // ---- Test 6: review unavailable ----

    [Fact]
    public void ReviewUnavailable_NotUsable_NotSemanticallyReviewed_IsNotAcceptedForPrint()
    {
        var accepted = DocumentationGeneratorModel.IsAcceptedForPrint(
            Review(usable: false, status: "review_unavailable"), Quality(semanticReviewCompleted: false));

        Assert.False(accepted);
    }

    // ---- Test 7: fallback / non-real-AI output ----

    [Theory]
    [InlineData("schema_invalid")]
    [InlineData("provider_unavailable")]
    [InlineData("firewall_blocked")]
    public void NotUsableStatuses_AreNeverAcceptedForPrint(string status)
    {
        var accepted = DocumentationGeneratorModel.IsAcceptedForPrint(
            Review(usable: false, status: status), Quality(semanticReviewCompleted: true));

        Assert.False(accepted);
    }

    // ---- Test 9: conflicting fields fail safely ----

    [Fact]
    public void ConflictingFields_UsableButSemanticReviewNotCompleted_IsNotAcceptedForPrint()
    {
        // The analogous "strong negative must override a weak positive"
        // case for this DTO shape: Usable=true alone is not enough to
        // claim full acceptance if semantic review never actually
        // completed.
        var accepted = DocumentationGeneratorModel.IsAcceptedForPrint(
            Review(usable: true, status: "approved"), Quality(semanticReviewCompleted: false));

        Assert.False(accepted);
    }

    [Fact]
    public void NullReviewOrQuality_IsNotAcceptedForPrint_FailsSafely()
    {
        Assert.False(DocumentationGeneratorModel.IsAcceptedForPrint(null, Quality(true)));
        Assert.False(DocumentationGeneratorModel.IsAcceptedForPrint(Review(true, "approved"), null));
        Assert.False(DocumentationGeneratorModel.IsAcceptedForPrint(null, null));
    }

    // ---- Unknown/unrecognized status with otherwise-positive fields fails safely ----

    [Theory]
    [InlineData("")]
    [InlineData("some_future_status_not_yet_handled")]
    public void UnknownStatus_WithUsableAndSemanticReviewCompletedTrue_IsNotAcceptedForPrint(string status)
    {
        // Not a broad "anything that isn't rejected" rule -- an
        // unrecognized status must never be silently treated as accepted
        // just because the two boolean fields happen to look positive.
        var accepted = DocumentationGeneratorModel.IsAcceptedForPrint(
            Review(usable: true, status: status), Quality(semanticReviewCompleted: true));

        Assert.False(accepted);
    }

    // ---- Conflicting positive fields plus a negative (non-accepted) status ----

    [Theory]
    [InlineData("rejected")]
    [InlineData("schema_invalid")]
    [InlineData("firewall_blocked")]
    public void ConflictingFields_UsableAndSemanticReviewCompletedTrue_ButNegativeStatus_IsNotAcceptedForPrint(string status)
    {
        // Usable=true and SemanticReviewCompleted=true both look positive,
        // but the authoritative Status is one of the real non-accepted
        // outcomes -- the explicit allowlist must still win.
        var accepted = DocumentationGeneratorModel.IsAcceptedForPrint(
            Review(usable: true, status: status), Quality(semanticReviewCompleted: true));

        Assert.False(accepted);
    }

    // ---- The printable detail row must always reflect the real status,
    // regardless of whether the headline is accepted -- i.e. the meta
    // block is never hidden/altered based on reviewAccepted. ----

    [Fact]
    public void PrintStatusMetaBlock_IsNotConditionalOnAcceptedHeadline()
    {
        var source = ReadDocumentationGeneratorCshtml();

        var sectionStart = source.IndexOf("class=\"doc-print-status\"", StringComparison.Ordinal);
        var metaStart = source.IndexOf("class=\"doc-print-status-meta\"", sectionStart, StringComparison.Ordinal);
        var sectionEnd = source.IndexOf("</section>", metaStart, StringComparison.Ordinal);

        Assert.True(sectionStart >= 0 && metaStart >= 0 && sectionEnd >= 0);

        // No "@if" between the section's own opening tag and the meta
        // block's opening tag would gate the WHOLE meta block -- it is
        // only individual optional notes further below (semantic-review-
        // incomplete / provenance warning) that are conditional, never the
        // Review status / Review detail rows themselves.
        var betweenSectionAndMeta = source[sectionStart..metaStart];
        Assert.DoesNotContain("@if (reviewAccepted", betweenSectionAndMeta, StringComparison.Ordinal);
    }

    // ------------------------------------------------------------------
    // Source-contract tests against the actual .cshtml file
    // ------------------------------------------------------------------

    private static string ReadDocumentationGeneratorCshtml()
    {
        var directory = new DirectoryInfo(AppContext.BaseDirectory);

        while (directory != null && !File.Exists(Path.Combine(directory.FullName, "FYPilot.sln")))
        {
            directory = directory.Parent;
        }

        Assert.True(directory != null, "Could not locate repository root (FYPilot.sln) from test output directory.");

        var path = Path.Combine(
            directory!.FullName, "src", "FYPilot.Web", "Pages", "Student", "DocumentationGenerator.cshtml");

        Assert.True(File.Exists(path), $"Expected to find DocumentationGenerator.cshtml at {path}.");

        return File.ReadAllText(path);
    }

    // ---- Test 1 / Test 10: warning container is printable, exact regression ----

    [Fact]
    public void PrintStatusSection_ExistsInsideSeReport_AndIsNotMarkedNoPrint()
    {
        var source = ReadDocumentationGeneratorCshtml();

        var reportStart = source.IndexOf("id=\"se-report\"", StringComparison.Ordinal);
        var sectionStart = source.IndexOf("class=\"doc-print-status\"", StringComparison.Ordinal);
        var passportStart = source.IndexOf("class=\"doc-passport no-print\"", StringComparison.Ordinal);

        Assert.True(reportStart >= 0, "#se-report was not found -- print CSS relies on this id existing.");
        Assert.True(sectionStart >= 0, "doc-print-status section was not found in the page.");

        // The status section must be INSIDE #se-report (after its opening
        // tag) so it is exempted from the print rule's blanket
        // `body * { visibility: hidden }`.
        Assert.True(sectionStart > reportStart,
            "doc-print-status must be located after #se-report's opening tag to survive print's visibility rule.");

        // The exact regression: the interactive on-screen passport is
        // still (correctly) marked no-print/hidden in print -- but the new
        // static summary must exist as a SEPARATE, non-no-print element,
        // never simply removing no-print from the interactive passport
        // itself (which would try to print a collapsible <details> panel).
        Assert.True(passportStart >= 0, "The on-screen Quality Passport should still exist, marked no-print.");

        // The new section's own opening tag must not carry no-print.
        var sectionTagEnd = source.IndexOf('>', sectionStart);
        var sectionOpenTag = source[(sectionStart - 30)..sectionTagEnd];
        Assert.DoesNotContain("no-print", sectionOpenTag, StringComparison.Ordinal);
    }

    [Fact]
    public void PrintStatusSection_ReusesExistingReviewAndProvenanceFields_NoNewClassifier()
    {
        var source = ReadDocumentationGeneratorCshtml();

        // Must be built from IsAcceptedForPrint / the existing quality and
        // provenance view-model fields -- never a freshly invented
        // condition inline in Razor.
        Assert.Contains("IsAcceptedForPrint(Model.LatestReview, quality)", source, StringComparison.Ordinal);
        Assert.Contains("quality?.ReviewStatusLabel", source, StringComparison.Ordinal);
        Assert.Contains("quality?.OutputReviewLevelLabel", source, StringComparison.Ordinal);
        Assert.Contains("provenance?.OutputOriginLabel", source, StringComparison.Ordinal);
    }

    // ---- Test 2: print-only summary is hidden on screen ----

    [Fact]
    public void PrintStatusClass_DefaultsToDisplayNone_OutsidePrintMedia()
    {
        var source = ReadDocumentationGeneratorCshtml();
        var mediaPrintIndex = source.IndexOf("@@media print", StringComparison.Ordinal);

        // Normalize whitespace so the check is independent of exact
        // line-ending/indentation formatting.
        var normalized = System.Text.RegularExpressions.Regex.Replace(source, @"\s+", " ");
        var baseRuleIndex = normalized.IndexOf(
            ".doc-print-cover, .doc-print-toc, .doc-print-status { display: none; }",
            StringComparison.Ordinal);

        Assert.True(baseRuleIndex >= 0,
            "Expected .doc-print-status to share the existing screen-hidden rule with .doc-print-cover/.doc-print-toc.");
        Assert.True(mediaPrintIndex > 0);

        // The base (screen) rule must be textually positioned before the
        // first @media print block in the un-normalized source too.
        var baseRuleIndexInSource = source.IndexOf(".doc-print-cover,", StringComparison.Ordinal);
        Assert.True(baseRuleIndexInSource >= 0 && baseRuleIndexInSource < mediaPrintIndex,
            "The screen-hide rule must appear before the first @media print block.");
    }

    // ---- Test 3: print-only summary appears in print ----

    [Fact]
    public void PrintStatusClass_BecomesVisible_InsidePrintMedia()
    {
        var source = ReadDocumentationGeneratorCshtml();
        var mediaPrintIndex = source.IndexOf("@@media print", StringComparison.Ordinal);
        Assert.True(mediaPrintIndex >= 0);

        // The rule INSIDE @media print, not the base (screen) rule that
        // shares the same selector text earlier in the file.
        var printRuleIndex = source.IndexOf(".doc-print-status {", mediaPrintIndex, StringComparison.Ordinal);

        Assert.True(printRuleIndex >= 0, "Expected an explicit .doc-print-status print rule inside @media print.");

        var snippet = source.Substring(printRuleIndex, Math.Min(400, source.Length - printRuleIndex));
        Assert.Contains("display: block !important;", snippet, StringComparison.Ordinal);
        Assert.Contains("break-inside: avoid;", snippet, StringComparison.Ordinal);
    }

    // ---- Test 4: interactive controls remain hidden in print ----

    [Fact]
    public void InteractiveControlsAndPassport_RemainHiddenInPrint()
    {
        var source = ReadDocumentationGeneratorCshtml();

        // The redundant explicit hide for the interactive passport/hero/
        // controls must still be present -- this task does not make the
        // interactive panel print, only adds the static summary.
        Assert.Contains(".doc-passport {", source, StringComparison.Ordinal);

        var mediaPrintBlocks = source.Split("@@media print");
        Assert.True(mediaPrintBlocks.Length >= 2);

        var secondPrintBlock = mediaPrintBlocks[^1];
        Assert.Contains(".doc-pro-controls", secondPrintBlock, StringComparison.Ordinal);
        Assert.Contains("display: none !important;", secondPrintBlock, StringComparison.Ordinal);

        // Export/regenerate buttons and toolbars stay under .no-print,
        // which the first @media print block already hides unconditionally.
        Assert.Contains("onclick=\"window.print()\"", source, StringComparison.Ordinal);
    }
}
