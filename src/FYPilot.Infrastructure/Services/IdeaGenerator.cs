using FYPilot.Domain.Entities;

namespace FYPilot.Infrastructure.Services;

public static class IdeaGenerator
{
    private static readonly Random Rng = new();

    private static readonly List<IdeaTemplate> Templates =
    [
        new("Smart Healthcare Appointment System",
            "Lebanese clinics and hospitals lack modern appointment management, causing long queues and inefficient scheduling.",
            "Patients, clinic staff, doctors",
            "Reduces wait times and improves patient experience for Lebanese healthcare facilities.",
            "Lebanese clinics and hospitals desperately need digitization. 80% still use phone booking.",
            "React, Node.js / ASP.NET Core, PostgreSQL, Twilio SMS",
            "JavaScript, SQL, APIs, HTML/CSS",
            "Healthcare", "Clinics",
            "Web", "beginner", "intermediate"),

        new("AI-Powered FYP Idea Recommender",
            "CS students struggle to find relevant, feasible final year project ideas matching their skills and career goals.",
            "CS students, university supervisors",
            "Guides students to pick the right project, reducing stress and increasing project quality.",
            "Lebanese universities (AUB, LAU, NDU, etc.) have thousands of CS students needing FYP guidance.",
            "Python, FastAPI, React, PostgreSQL, scikit-learn",
            "Python, Machine Learning, APIs, SQL",
            "AI/Data Science", "Universities", "AI/Data Science", "advanced", "advanced"),

        new("Lebanese Restaurant Online Ordering Platform",
            "Small Lebanese restaurants lose customers to large platforms that charge high commissions (30%+).",
            "Restaurant owners, customers",
            "Enables direct online ordering with no commission, helping small restaurants survive.",
            "Lebanon has thousands of small restaurants being undercut by food delivery platforms.",
            "React, Node.js, Stripe, PostgreSQL, Firebase",
            "JavaScript, SQL, APIs, HTML/CSS",
            "Web Development", "Restaurants", "Web", "intermediate", "intermediate"),

        new("Real-Time Traffic & Route Optimizer for Lebanon",
            "Beirut traffic is among the worst globally. No reliable real-time routing app exists for Lebanon.",
            "Drivers, commuters, delivery services",
            "Saves hours of commuting time for thousands of Lebanese drivers daily.",
            "Lebanon has severe traffic problems — a local routing solution could be monetized for delivery companies.",
            "React Native, Google Maps API, Node.js, PostgreSQL, Python",
            "JavaScript, APIs, Python, Databases",
            "Mobile", "Logistics", "Mobile", "advanced", "advanced"),

        new("University Course Recommendation System",
            "Students struggle to choose electives and plan their academic path optimally.",
            "University students, academic advisors",
            "Improves graduation rates and satisfaction by recommending optimal course paths.",
            "Lebanese universities can reduce dropout rates by helping students plan better.",
            "Python, Flask/FastAPI, React, PostgreSQL, collaborative filtering",
            "Python, Machine Learning, SQL, APIs",
            "AI/Data Science", "Education", "AI/Data Science", "intermediate", "intermediate"),

        new("Smart Inventory Management for Lebanese Supermarkets",
            "Small supermarkets in Lebanon manage inventory manually, leading to overstocking or stockouts.",
            "Supermarket owners, staff",
            "Reduces waste and increases profits for Lebanese small businesses.",
            "Lebanon has thousands of small supermarkets that could benefit from affordable inventory software.",
            "C# ASP.NET Core, React, PostgreSQL, barcode scanning",
            "C#, SQL, JavaScript, APIs",
            ".NET Development", "Small Businesses", "Web", "intermediate", "intermediate"),

        new("E-Learning Platform for Lebanese Students",
            "COVID-19 exposed the lack of quality online learning platforms adapted to the Lebanese curriculum.",
            "K-12 students, university students, teachers",
            "Provides accessible education to Lebanese students regardless of location or economic status.",
            "Lebanon's education crisis and diaspora create huge demand for quality Arabic/English e-learning.",
            "React, Node.js, PostgreSQL, WebRTC, AWS S3",
            "JavaScript, SQL, APIs, HTML/CSS, Cloud",
            "Web Development", "Education", "Web", "advanced", "advanced"),

        new("NGO Volunteer Management System",
            "Lebanese NGOs (especially post-2020 blast) struggle to coordinate hundreds of volunteers efficiently.",
            "NGO coordinators, volunteers",
            "Streamlines volunteer matching, scheduling, and impact reporting for Lebanese NGOs.",
            "Lebanon has 5,000+ NGOs with poor digital tools. Crisis response requires effective coordination.",
            "React, Node.js, PostgreSQL, Google Maps API",
            "JavaScript, SQL, APIs, HTML/CSS",
            "Web Development", "NGOs", "Web", "intermediate", "intermediate"),

        new("AI-Based Cybersecurity Threat Detection",
            "Lebanese SMEs lack affordable cybersecurity solutions and face increasing cyberattacks.",
            "IT managers, SME owners",
            "Provides affordable AI-driven threat detection to Lebanese businesses.",
            "Lebanese businesses lost millions to cyberattacks. Affordable local solutions are needed.",
            "Python, TensorFlow, FastAPI, PostgreSQL, ELK Stack",
            "Python, Machine Learning, SQL, Cloud",
            "AI/Data Science", "Banking", "AI/Data Science", "advanced", "advanced"),

        new("Smart Real Estate Price Predictor for Lebanon",
            "Lebanon real estate market lacks data-driven pricing tools, causing buyer/seller information asymmetry.",
            "Home buyers, sellers, real estate agents",
            "Gives fair price estimates based on location, size, and market trends.",
            "Lebanon real estate market is opaque. A data-driven pricing tool would be revolutionary.",
            "Python, scikit-learn, FastAPI, React, PostgreSQL, web scraping",
            "Python, Machine Learning, Data Analysis, SQL, APIs",
            "AI/Data Science", "Real Estate", "AI/Data Science", "intermediate", "advanced"),

        new("Fashion Store Management & E-Commerce Platform",
            "Lebanese fashion boutiques have no affordable integrated e-commerce + inventory solution.",
            "Fashion boutique owners, customers",
            "Enables local fashion brands to go online with full inventory, orders, and payment management.",
            "Lebanon has a thriving fashion industry but most boutiques lack professional e-commerce.",
            "React, C# ASP.NET Core, PostgreSQL, Stripe, Cloudinary",
            "C#, JavaScript, SQL, APIs, HTML/CSS",
            ".NET Development", "Fashion", "Web", "intermediate", "intermediate"),

        new("IoT Smart Home System for Lebanese Apartments",
            "Lebanese households face power outages and generator issues. Smart home automation can help.",
            "Lebanese homeowners, renters",
            "Automates power switching, monitors consumption, and controls appliances remotely.",
            "Lebanon has 20+ hours of power cuts daily. Smart energy management is critical.",
            "Arduino, Raspberry Pi, MQTT, Node.js, React, Python",
            "Python, APIs, Cloud, Databases",
            "IoT", "Small Businesses", "Web", "advanced", "advanced"),

        new("Banking Fraud Detection System",
            "Lebanese banks and fintech companies need affordable AI-based fraud detection to protect customers.",
            "Banks, fintech companies, customers",
            "Reduces financial fraud losses for Lebanese banks using ML anomaly detection.",
            "Lebanon banking crisis exposed massive fraud risk. Banks urgently need detection tools.",
            "Python, scikit-learn, FastAPI, PostgreSQL, React",
            "Python, Machine Learning, Data Analysis, SQL",
            "AI/Data Science", "Banking", "AI/Data Science", "advanced", "advanced"),

        new("Delivery Tracking & Logistics App",
            "Lebanese delivery businesses (post-explosion boom) lack professional tracking and dispatch tools.",
            "Delivery companies, drivers, customers",
            "Provides real-time package tracking, driver dispatch, and customer notifications.",
            "Lebanon's delivery market boomed post-2019. Professional tools are scarce and expensive.",
            "React Native, Node.js, Google Maps, PostgreSQL, Firebase",
            "JavaScript, APIs, Databases, Mobile",
            "Mobile", "Delivery", "Mobile", "intermediate", "advanced"),

        new("Student Attendance & Grade Management System",
            "Lebanese school and university teachers spend excessive time on attendance and grade management.",
            "Teachers, administrators, students, parents",
            "Automates attendance, grade tracking, and parent notifications for Lebanese schools.",
            "Lebanon has 1,000+ schools needing affordable management software.",
            "C# ASP.NET Core, React, PostgreSQL, QR Code scanning",
            "C#, JavaScript, SQL, APIs",
            ".NET Development", "Education", "Web", "beginner", "intermediate"),
    ];

    public static List<IdeaData> Generate(string major, string level, string domain, string difficulty,
        string stack, int hours, int team, List<string> ownedSkills)
    {
        var filtered = Templates.Where(t =>
            (stack == "Any" || t.PreferredStack == stack || t.Domain.Contains(stack, StringComparison.OrdinalIgnoreCase))
            && (difficulty == "any" || t.DifficultyLevel == difficulty || difficulty == "intermediate")
        ).ToList();

        if (!filtered.Any()) filtered = Templates.ToList();

        var selected = filtered.OrderBy(_ => Rng.Next()).Take(3).ToList();

        return selected.Select(t => BuildIdea(t, ownedSkills, hours)).ToList();
    }

    private static IdeaData BuildIdea(IdeaTemplate t, List<string> ownedSkills, int hours)
    {
        var requiredList = t.RequiredSkills.Split(", ").ToList();
        var missing = requiredList.Where(s => !ownedSkills.Contains(s)).ToList();
        var skillMatch = requiredList.Count > 0
            ? (int)((double)(requiredList.Count - missing.Count) / requiredList.Count * 100)
            : 70;

        var weeks = hours >= 30 ? 12 : hours >= 20 ? 16 : 20;
        var innovation = Rng.Next(65, 92);
        var market = Rng.Next(70, 95);
        var feasibility = (skillMatch + innovation + market) / 3;

        return new IdeaData
        {
            Title = t.Title,
            ProblemStatement = t.ProblemStatement,
            TargetUsers = t.TargetUsers,
            WhyUseful = t.WhyUseful,
            LebaneseMarketRelevance = t.LebaneseMarketRelevance,
            RequiredTechnologies = t.RequiredTechnologies,
            RequiredSkills = t.RequiredSkills,
            MissingSkills = string.Join(", ", missing),
            DifficultyLevel = t.DifficultyLevel,
            InnovationScore = innovation,
            FeasibilityScore = feasibility,
            MarketDemandScore = market,
            ExpectedDurationWeeks = weeks,
            SupervisorCategory = t.Domain,
            DatasetNeeded = t.Domain.Contains("AI") ? "Public datasets (Kaggle, UCI) + scraped Lebanese data" : "None",
            FinalDeliverables = $"Web/Mobile Application, API Documentation, Database Schema, Testing Report, Final Presentation",
            Domain = t.Domain,
            LebanesesSector = t.LebanesesSector,
        };
    }
}

public record IdeaTemplate(
    string Title, string ProblemStatement, string TargetUsers,
    string WhyUseful, string LebaneseMarketRelevance, string RequiredTechnologies,
    string RequiredSkills, string Domain, string LebanesesSector,
    string PreferredStack, string DifficultyLevel, string RecommendedLevel
);

public class IdeaData
{
    public string Title { get; set; } = "";
    public string ProblemStatement { get; set; } = "";
    public string TargetUsers { get; set; } = "";
    public string WhyUseful { get; set; } = "";
    public string LebaneseMarketRelevance { get; set; } = "";
    public string RequiredTechnologies { get; set; } = "";
    public string RequiredSkills { get; set; } = "";
    public string MissingSkills { get; set; } = "";
    public string DifficultyLevel { get; set; } = "intermediate";
    public int InnovationScore { get; set; }
    public int FeasibilityScore { get; set; }
    public int MarketDemandScore { get; set; }
    public int ExpectedDurationWeeks { get; set; }
    public string SupervisorCategory { get; set; } = "";
    public string DatasetNeeded { get; set; } = "";
    public string FinalDeliverables { get; set; } = "";
    public string Domain { get; set; } = "";
    public string LebanesesSector { get; set; } = "";
}
