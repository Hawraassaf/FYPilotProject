using FYPilot.Api.Controllers;
using Microsoft.AspNetCore.Http;
using Microsoft.AspNetCore.Mvc;
using Xunit;

namespace FYPilot.Tests.Roadmap;

/// <summary>
/// The legacy /api/roadmap controller generated hardcoded, non-AI, non-
/// reviewed content and wrote to the same tables as the real Roadmap
/// pipeline (Razor Page -> AI service -> ProjectRoadmapAgent). It must never
/// be able to create or overwrite roadmap data again -- every route now
/// returns 410 Gone instead of touching the database.
/// </summary>
public class RoadmapControllerTests
{
    [Fact]
    public void Get_ReturnsGone()
    {
        var controller = new RoadmapController();

        var result = Assert.IsType<ObjectResult>(controller.Get(1));

        Assert.Equal(StatusCodes.Status410Gone, result.StatusCode);
    }

    [Fact]
    public void Generate_ReturnsGone_AndNeverCreatesARoadmap()
    {
        var controller = new RoadmapController();

        var result = Assert.IsType<ObjectResult>(controller.Generate(1));

        Assert.Equal(StatusCodes.Status410Gone, result.StatusCode);
    }

    [Fact]
    public void MarkComplete_ReturnsGone()
    {
        var controller = new RoadmapController();

        var result = Assert.IsType<ObjectResult>(controller.MarkComplete(1));

        Assert.Equal(StatusCodes.Status410Gone, result.StatusCode);
    }
}
