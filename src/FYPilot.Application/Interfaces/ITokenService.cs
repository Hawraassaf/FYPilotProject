using FYPilot.Domain.Entities;

namespace FYPilot.Application.Interfaces;

/// <summary>
/// Contract for generating JWT tokens for authenticated users.
/// </summary>
public interface ITokenService
{
    string GenerateToken(User user);
}
