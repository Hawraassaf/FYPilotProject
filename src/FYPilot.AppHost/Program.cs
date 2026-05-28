// FYPilot.AppHost — Orchestration entry point
//
// Purpose: Documents the full run configuration for Visual Studio.
// Each component can also be run manually — see docs/VISUAL_STUDIO_SETUP.md.
//
// OPTION A — Run from Visual Studio:
//   1. Set FYPilot.AppHost as the Startup Project
//   2. Run the Python AI service manually first (it cannot be launched by this AppHost):
//        cd services/FYPilot.AI
//        python run.py
//   3. Press F5 — .NET API starts on http://localhost:8080
//   4. In a separate terminal, start the React frontend:
//        cd src/FYPilot.Web && npm run dev
//   5. Open http://localhost:3000/system-test
//
// OPTION B — Manual (3 terminals):
//   Terminal 1: cd services/FYPilot.AI && python run.py
//   Terminal 2: cd src/FYPilot.Api && dotnet run --urls http://localhost:8080
//   Terminal 3: cd src/FYPilot.Web && npm run dev

Console.WriteLine("=================================================");
Console.WriteLine("  FYPilot AppHost");
Console.WriteLine("=================================================");
Console.WriteLine();
Console.WriteLine("This AppHost documents the FYPilot run configuration.");
Console.WriteLine("To start the full stack, run each service manually:");
Console.WriteLine();
Console.WriteLine("  Python AI service:");
Console.WriteLine("    cd services/FYPilot.AI");
Console.WriteLine("    python run.py");
Console.WriteLine();
Console.WriteLine("  .NET API:");
Console.WriteLine("    cd src/FYPilot.Api");
Console.WriteLine("    dotnet run --urls http://localhost:8080");
Console.WriteLine();
Console.WriteLine("  React frontend:");
Console.WriteLine("    cd src/FYPilot.Web");
Console.WriteLine("    npm run dev");
Console.WriteLine();
Console.WriteLine("  System test: http://localhost:3000/system-test");
Console.WriteLine("=================================================");
