using FYPilot.Domain.Entities;
using Microsoft.EntityFrameworkCore;

namespace FYPilot.Infrastructure.Data;

public static class DataSeeder
{
    public static async Task SeedAsync(ApplicationDbContext db)
    {
        if (!await db.Users.AnyAsync())
        {
            var users = new[]
            {
                new User { Email = "student@fyp.com",    FullName = "Ahmad Khalil",    Role = "student",    PasswordHash = BCrypt.Net.BCrypt.HashPassword("password123") },
                new User { Email = "supervisor@fyp.com", FullName = "Dr. Rania Hassan", Role = "supervisor", PasswordHash = BCrypt.Net.BCrypt.HashPassword("password123") },
                new User { Email = "admin@fyp.com",      FullName = "Admin User",       Role = "admin",      PasswordHash = BCrypt.Net.BCrypt.HashPassword("password123") },
                new User { Email = "student2@fyp.com",   FullName = "Sara Mansour",     Role = "student",    PasswordHash = BCrypt.Net.BCrypt.HashPassword("password123") },
                new User { Email = "supervisor2@fyp.com",FullName = "Dr. Karim Nasser", Role = "supervisor", PasswordHash = BCrypt.Net.BCrypt.HashPassword("password123") },
            };
            db.Users.AddRange(users);
            await db.SaveChangesAsync();

            db.StudentProfiles.AddRange(
                new StudentProfile { UserId = users[0].Id, University = "American University of Beirut", Major = "Computer Science", Year = "3rd Year", ExperienceLevel = "intermediate", PreferredDomain = "Web Development", PreferredStack = "Web", AvailableHoursPerWeek = 25, TeamMembers = 2, TargetDifficulty = "intermediate", ProjectGoals = "Build a production-ready web application with AI features", Interests = "AI, Web Development, Lebanese market applications", Skills = "JavaScript, Python, SQL" },
                new StudentProfile { UserId = users[3].Id, University = "Lebanese American University",  Major = "Computer Engineering", Year = "4th Year", ExperienceLevel = "advanced",      PreferredDomain = "AI/Data Science",  PreferredStack = "AI/Data Science", AvailableHoursPerWeek = 30, TeamMembers = 1, TargetDifficulty = "advanced",      ProjectGoals = "Develop an ML-based system for the Lebanese healthcare sector", Interests = "Machine Learning, Healthcare, Data Science", Skills = "Python, Machine Learning, SQL" }
            );

            db.SupervisorProfiles.AddRange(
                new SupervisorProfile { UserId = users[1].Id, Department = "Computer Science", Specialization = "AI and Machine Learning",       Bio = "Dr. Hassan has 12 years of experience in AI research and has supervised 40+ FYP projects." },
                new SupervisorProfile { UserId = users[4].Id, Department = "Computer Science", Specialization = "Web Technologies and Cloud",     Bio = "Dr. Nasser specializes in full-stack web development and cloud-native architectures." }
            );

            await db.SaveChangesAsync();
        }

        if (!await db.MarketNeeds.AnyAsync())
        {
            db.MarketNeeds.AddRange(
                new MarketNeed { Sector = "Healthcare",  Problem = "Lebanese clinics lack digital appointment management systems",         PossibleSolution = "Smart appointment booking platform with SMS reminders",       BusinessValue = "Reduce no-shows by 30%, serve more patients efficiently", DemandScore = 92 },
                new MarketNeed { Sector = "Education",   Problem = "Remote learning platforms not adapted to Lebanese curriculum",          PossibleSolution = "Arabic/English e-learning platform with Lebanese syllabus",  BusinessValue = "Reach 200,000+ Lebanese students; subscription model",      DemandScore = 88 },
                new MarketNeed { Sector = "Banking",     Problem = "Lebanese banks need fraud detection systems post-crisis",              PossibleSolution = "AI-powered transaction anomaly detection",                    BusinessValue = "Prevent millions in fraud losses; mandatory compliance",    DemandScore = 95 },
                new MarketNeed { Sector = "Delivery",    Problem = "Local delivery companies lack professional tracking tools",            PossibleSolution = "Real-time package tracking and dispatch platform",           BusinessValue = "Serve Lebanon's booming post-2019 delivery market",         DemandScore = 85 },
                new MarketNeed { Sector = "E-Commerce",  Problem = "Small Lebanese retailers have no affordable e-commerce solution",      PossibleSolution = "Easy-to-use online store builder with local payment methods",BusinessValue = "Enable thousands of SMEs to sell online",                   DemandScore = 87 },
                new MarketNeed { Sector = "NGOs",        Problem = "Lebanese NGOs struggle to coordinate volunteers post-Beirut blast",   PossibleSolution = "Volunteer management and disaster response platform",        BusinessValue = "Improve coordination for 5,000+ active NGOs in Lebanon",   DemandScore = 83 },
                new MarketNeed { Sector = "Restaurants", Problem = "Small restaurants lose 30%+ to food delivery platform commissions",   PossibleSolution = "Commission-free direct ordering platform",                   BusinessValue = "Save restaurants thousands monthly; increase margins",      DemandScore = 89 },
                new MarketNeed { Sector = "Real Estate", Problem = "Opaque real estate pricing causes buyer/seller inefficiency",          PossibleSolution = "AI-powered property price prediction platform",              BusinessValue = "Bring transparency to Lebanon's $2B+ real estate market",   DemandScore = 81 }
            );
            await db.SaveChangesAsync();
        }

        if (!await db.PreviousProjects.AnyAsync())
        {
            db.PreviousProjects.AddRange(
                new PreviousProject { Title = "Online Appointment Booking System",    Description = "Web platform for booking appointments.",              Domain = "Web Development",  Technologies = "PHP, MySQL, Bootstrap",       Year = 2022, University = "AUB" },
                new PreviousProject { Title = "E-Commerce Website for Lebanese Brands", Description = "Online store for Lebanese artisans.",             Domain = "Web Development",  Technologies = "React, Node.js, MongoDB",     Year = 2023, University = "LAU" },
                new PreviousProject { Title = "Machine Learning Disease Prediction",  Description = "ML model to predict diseases from symptoms.",         Domain = "AI/Data Science",  Technologies = "Python, scikit-learn, Flask", Year = 2023, University = "AUB" },
                new PreviousProject { Title = "Food Delivery Tracking App",           Description = "Mobile app for tracking food delivery orders.",        Domain = "Mobile",           Technologies = "React Native, Firebase",      Year = 2022, University = "BAU" },
                new PreviousProject { Title = "Fraud Detection System for Banking",   Description = "Rule-based fraud detection for credit card transactions.", Domain = "AI/Data Science", Technologies = "Python, pandas, scikit-learn", Year = 2022, University = "AUB" },
                new PreviousProject { Title = "Volunteer Coordination Platform",      Description = "Platform for NGOs to manage volunteer activities.",    Domain = "Web Development",  Technologies = "React, Node.js, PostgreSQL",  Year = 2023, University = "AUL" }
            );
            await db.SaveChangesAsync();
        }
    }
}
