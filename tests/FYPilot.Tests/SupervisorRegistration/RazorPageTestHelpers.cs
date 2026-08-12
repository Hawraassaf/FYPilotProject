using System.Security.Claims;
using Microsoft.AspNetCore.Http;
using Microsoft.AspNetCore.Mvc.ModelBinding;
using Microsoft.AspNetCore.Mvc.RazorPages;
using Microsoft.AspNetCore.Mvc.ViewFeatures;
using Microsoft.AspNetCore.WebUtilities;
using Microsoft.Extensions.DependencyInjection;

namespace FYPilot.Tests.SupervisorRegistration;

/// <summary>
/// Minimal, self-contained plumbing to unit-test Razor PageModels
/// (Register, VerifySupervisorApplication, SupervisorApplicationDetails)
/// outside of a real HTTP request. Nothing in this project's existing
/// tests exercises a POST PageModel handler that reads/writes raw
/// TempData["..."] or ModelState, so this file provides the smallest
/// working substitutes rather than pulling in a full WebApplicationFactory.
/// </summary>
internal static class RazorPageTestHelpers
{
    /// <summary>
    /// Corrupts a Data Protection token deterministically for tamper
    /// tests. Flipping a character-level position near the end of the
    /// base64url string is unreliable: depending on the token's byte
    /// length modulo 3, the trailing base64 character can carry
    /// redundant/padding bits that survive re-decoding unchanged,
    /// producing a flaky test. Decoding to raw bytes and flipping a
    /// full byte in the middle of the ciphertext avoids that edge
    /// case entirely and reliably fails AES-GCM tag verification.
    /// </summary>
    public static string TamperToken(string token)
    {
        var bytes = WebEncoders.Base64UrlDecode(token);
        bytes[bytes.Length / 2] ^= 0xFF;
        return WebEncoders.Base64UrlEncode(bytes);
    }


    public static PageContext CreateAnonymousPageContext()
    {
        var services = new ServiceCollection();
        services.AddSingleton<ITempDataDictionaryFactory, FakeTempDataDictionaryFactory>();
        var provider = services.BuildServiceProvider();

        var httpContext = new DefaultHttpContext
        {
            RequestServices = provider,
            User = new ClaimsPrincipal(new ClaimsIdentity())
        };

        return new PageContext
        {
            HttpContext = httpContext,
            ViewData = new ViewDataDictionary(
                new EmptyModelMetadataProvider(),
                new ModelStateDictionary())
        };
    }

    public static PageContext CreateAuthenticatedPageContext(
        int userId,
        string role)
    {
        var context = CreateAnonymousPageContext();

        var claims = new List<Claim>
        {
            new(ClaimTypes.NameIdentifier, userId.ToString()),
            new(ClaimTypes.Role, role)
        };

        context.HttpContext.User = new ClaimsPrincipal(
            new ClaimsIdentity(claims, "TestAuthentication"));

        return context;
    }

    private sealed class FakeTempDataDictionaryFactory : ITempDataDictionaryFactory
    {
        public ITempDataDictionary GetTempData(HttpContext context)
        {
            return new FakeTempDataDictionary();
        }
    }

    private sealed class FakeTempDataDictionary : Dictionary<string, object?>,
        ITempDataDictionary
    {
        public void Keep()
        {
        }

        public void Keep(string key)
        {
        }

        public void Load()
        {
        }

        public void Save()
        {
        }

        public object? Peek(string key)
        {
            return TryGetValue(key, out var value) ? value : null;
        }
    }
}
