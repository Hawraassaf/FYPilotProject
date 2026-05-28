using FYPilot.Application.DTOs;
using FYPilot.Domain.Entities;

namespace FYPilot.Infrastructure.Services;

public static class PlanGenerator
{
    public static ImplementationPlanResponse GenerateDotNetPlan(ProjectIdea idea)
    {
        var safeTitle = idea.Title.Replace(" ", "");
        return new ImplementationPlanResponse(
            idea.Title,
            "Clean Architecture with ASP.NET Core 8 Web API, Entity Framework Core, JWT Authentication, and Repository Pattern",
            $@"FypPlatform.sln
└── {safeTitle}.Api/
    ├── Controllers/     # HTTP route handlers (thin — delegate to services)
    ├── Data/            # ApplicationDbContext (EF Core)
    ├── DTOs/            # Request & Response models
    ├── Models/          # Database entity classes
    ├── Repositories/    # Data access abstractions
    ├── Services/        # Business logic
    ├── Middlewares/     # Auth, error handling
    └── Program.cs       # App startup & DI config",
            idea.Domain.Contains("Mobile") ? new List<string> { "AuthController", "ProfileController", "SyncController" } : new List<string> { "AuthController", "ProfileController", "ProjectController", "AnalyticsController", "AdminController" },
            [$"I{safeTitle}Service", $"{safeTitle}Service", "TokenService", "EmailService"],
            ["User", "Profile", "Project", "ActivityLog"],
            ["POST /api/auth/login", "POST /api/auth/register", "GET /api/auth/me",
             $"GET /api/{idea.Domain.ToLower().Replace(" ", "-")}", $"POST /api/{idea.Domain.ToLower().Replace(" ", "-")}",
             "GET /api/dashboard", "POST /api/admin/seed"],
            "JWT Bearer tokens — BCrypt password hashing, token expiry 7 days, role-based [Authorize(Roles=...)] attributes",
            "FluentValidation + Data Annotations on DTOs. Return 400 Bad Request with field errors.",
            "Global exception middleware returning RFC 7807 ProblemDetails. Log via Serilog.",
            "xUnit + Moq for unit tests. WebApplicationFactory for integration tests. Aim 80%+ coverage on Services.",
            "Docker → GitHub Actions CI/CD → Railway/Azure App Service. PostgreSQL via managed service. Env vars for secrets.",
            $@"public class ApplicationDbContext : DbContext
{{
    public DbSet<User> Users {{ get; set; }}
    public DbSet<Profile> Profiles {{ get; set; }}
    // Add your domain entities here
    
    protected override void OnModelCreating(ModelBuilder mb)
    {{
        mb.Entity<User>().HasIndex(u => u.Email).IsUnique();
        // Configure relationships here
    }}
}}"
        );
    }

    public static DataSciencePlanResponse GeneratePythonPlan(ProjectIdea idea)
    {
        var isAi = idea.Domain.Contains("AI") || idea.Domain.Contains("Data Science");
        return new DataSciencePlanResponse(
            idea.Title,
            isAi
                ? ["Kaggle public datasets", "Lebanese open data portals", "Web scraping (BeautifulSoup/Scrapy)", "Synthetic data generation"]
                : ["Application database (PostgreSQL)", "User activity logs", "API responses"],
            isAi
                ? "Features: id, timestamp, category, numerical_features[], label (target). Format: CSV initially, then loaded into pandas DataFrame."
                : "Analytics tables: aggregated daily/weekly metrics, user behavior logs.",
            ["Remove null/duplicate rows", "Normalize numerical features (StandardScaler)", "Encode categorical variables (LabelEncoder/OneHotEncoder)", "Handle class imbalance (SMOTE if needed)", "Train/val/test split (70/15/15)"],
            isAi
                ? ["Correlation analysis", "PCA for dimensionality reduction", "Domain-specific feature creation", "Time-based features (if time series)"]
                : ["Aggregation features", "Rolling averages", "Rate calculations"],
            isAi
                ? "Start with RandomForest/XGBoost (interpretable, robust). Try LogisticRegression as baseline. If deep features needed, use a small Neural Network. Evaluate with cross-validation."
                : "Descriptive analytics + trend analysis. No ML needed — focus on data aggregation and visualization.",
            isAi ? ["Accuracy", "F1-Score", "ROC-AUC", "Precision/Recall", "Confusion Matrix"] : ["MAE", "RMSE", "R²"],
            "1. Load data → 2. Preprocess → 3. Feature engineering → 4. Train model → 5. Evaluate → 6. Save model (joblib) → 7. Version with MLflow",
            "Load saved model → Preprocess input → Predict → Return JSON response via FastAPI",
            ".NET backend calls Python FastAPI service via HTTP: POST /predict with JSON body. Python returns prediction + confidence. Use HttpClient in .NET with retry policies.",
            @"python-service/
├── app/
│   ├── main.py          # FastAPI app entry point
│   ├── routers/
│   │   └── predict.py   # Prediction endpoints
│   ├── services/
│   │   ├── preprocessor.py
│   │   └── model_service.py
│   ├── models/
│   │   └── schemas.py   # Pydantic request/response models
│   └── data/
│       └── sample_data.csv
├── notebooks/
│   └── 01_eda_and_training.ipynb
├── requirements.txt
└── run.py"
        );
    }
}
