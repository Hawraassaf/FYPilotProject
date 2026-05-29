"""
Project Idea Generation Router
Endpoint for generating intelligent FYP ideas based on student profile and skills.
"""
import logging
import httpx
import json
from typing import Any, Dict, List, Optional
from fastapi import APIRouter

logger = logging.getLogger("fypilot-ideas")

router = APIRouter()


class ProjectIdeaAgent:
    """
    Agent for generating intelligent FYP project ideas based on student profile.
    Uses deterministic rule-based scoring with optional Ollama fallback for generation.
    """

    def __init__(self):
        self.ollama_url = "http://localhost:11434/api/generate"
        self.ollama_model = "phi3"

    def _call_ollama(self, prompt: str) -> Optional[str]:
        """
        Attempt to call local Ollama API. Returns None if unavailable.
        Never crashes if Ollama is down.
        """
        try:
            with httpx.Client(timeout=5.0) as client:
                response = client.post(
                    self.ollama_url,
                    json={"model": self.ollama_model, "prompt": prompt, "stream": False},
                )
                if response.status_code == 200:
                    data = response.json()
                    return data.get("response", "").strip()
        except Exception as e:
            logger.warning(f"Ollama unavailable: {e}. Using rule-based generation.")
        return None

    def _calculate_innovation_score(
        self, skills_rating: float, target_difficulty: str, preferred_domain: str
    ) -> float:
        """
        Calculate innovation score based on student skills and project difficulty.
        Range: 0-100
        """
        base_score = skills_rating * 0.6

        difficulty_bonus = {
            "beginner": 10,
            "intermediate": 30,
            "advanced": 50,
            "expert": 70,
        }
        difficulty_multiplier = difficulty_bonus.get(target_difficulty.lower(), 30) / 100

        domain_innovation = {
            "web": 40,
            "mobile": 50,
            "ai_ml": 80,
            "iot": 70,
            "blockchain": 85,
            "cloud": 50,
            "data_science": 75,
            "cybersecurity": 70,
        }
        domain_bonus = domain_innovation.get(preferred_domain.lower(), 40)

        innovation_score = min(
            (base_score + (difficulty_multiplier * 50) + domain_bonus / 2), 100
        )
        return round(innovation_score, 2)

    def _calculate_feasibility_score(
        self,
        available_hours: float,
        team_members: int,
        target_difficulty: str,
        experience_level: str,
    ) -> float:
        """
        Calculate feasibility score based on resources and experience.
        Range: 0-100
        """
        hours_per_person = available_hours / max(team_members, 1)
        hours_score = min((hours_per_person / 15) * 40, 40)

        team_efficiency = min(team_members * 10, 30)

        experience_multiplier = {
            "beginner": 0.6,
            "intermediate": 0.85,
            "advanced": 1.0,
            "expert": 1.1,
        }
        exp_factor = experience_multiplier.get(experience_level.lower(), 0.85)

        difficulty_reduction = {
            "beginner": 20,
            "intermediate": 0,
            "advanced": -10,
            "expert": -20,
        }
        diff_penalty = difficulty_reduction.get(target_difficulty.lower(), 0)

        feasibility_score = min(
            (hours_score + team_efficiency) * exp_factor + diff_penalty, 100
        )
        return max(round(feasibility_score, 2), 10)

    def _calculate_market_demand_score(
        self, preferred_domain: str, skills_rating: float, major: str
    ) -> float:
        """
        Calculate market demand score based on domain relevance.
        Range: 0-100
        """
        domain_demand = {
            "ai_ml": 95,
            "data_science": 90,
            "blockchain": 60,
            "cybersecurity": 85,
            "web": 70,
            "mobile": 75,
            "iot": 65,
            "cloud": 80,
        }
        base_demand = domain_demand.get(preferred_domain.lower(), 50)

        skills_factor = (skills_rating / 100) * 20

        major_alignment = 15 if major.lower() in ["cs", "cse", "computer science"] else 5

        market_demand = min(base_demand + skills_factor + major_alignment, 100)
        return round(market_demand, 2)

    def _get_required_skills(self, preferred_domain: str) -> List[str]:
        """Get typical required skills for a domain."""
        domain_skills = {
            "web": [
                "React/Vue",
                "Node.js/Python",
                "PostgreSQL",
                "RESTful APIs",
                "CSS/HTML",
            ],
            "mobile": [
                "Flutter/React Native",
                "Android/iOS",
                "Mobile UX",
                "API Integration",
            ],
            "ai_ml": [
                "Python",
                "TensorFlow/PyTorch",
                "Data Preprocessing",
                "Model Training",
                "Statistics",
            ],
            "iot": [
                "Embedded C/C++",
                "Arduino/Raspberry Pi",
                "Sensor Integration",
                "IoT Protocols",
            ],
            "blockchain": [
                "Solidity",
                "Smart Contracts",
                "Cryptography",
                "Consensus Algorithms",
            ],
            "cloud": [
                "AWS/Azure/GCP",
                "Docker",
                "Kubernetes",
                "Microservices",
                "CI/CD",
            ],
            "data_science": [
                "Python",
                "Pandas/NumPy",
                "Data Visualization",
                "Statistical Analysis",
                "Big Data Tools",
            ],
            "cybersecurity": [
                "Network Security",
                "Penetration Testing",
                "Cryptography",
                "Security Tools",
            ],
        }
        return domain_skills.get(preferred_domain.lower(), ["Python", "Problem Solving"])

    def _get_missing_skills(
        self, required_skills: List[str], student_skills: List[str]
    ) -> List[str]:
        """Identify skills student needs to learn."""
        student_skills_lower = [s.lower() for s in student_skills]
        missing = [s for s in required_skills if s.lower() not in student_skills_lower]
        return missing

    def generate_ideas(self, profile: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Generate 3 intelligent FYP ideas based on student profile.
        
        Args:
            profile: Student profile containing:
                - major, experienceLevel, preferredDomain, targetDifficulty
                - preferredStack, availableHoursPerWeek, teamMembers, projectGoals
                - skills (list of dicts with SkillName, Rating, ProficiencyLevel)
        
        Returns:
            List of 3 project ideas with all required fields
        """
        # Extract profile data with case-insensitive key handling
        major = profile.get("major") or profile.get("Major", "CS")
        experience_level = (
            profile.get("experienceLevel")
            or profile.get("ExperienceLevel", "intermediate")
        )
        preferred_domain = (
            profile.get("preferredDomain") or profile.get("PreferredDomain", "web")
        )
        target_difficulty = (
            profile.get("targetDifficulty")
            or profile.get("TargetDifficulty", "intermediate")
        )
        preferred_stack = (
            profile.get("preferredStack") or profile.get("PreferredStack", "")
        )
        available_hours = float(
            profile.get("availableHoursPerWeek")
            or profile.get("AvailableHoursPerWeek", 20)
        )
        team_members = int(
            profile.get("teamMembers") or profile.get("TeamMembers", 1)
        )
        project_goals = (
            profile.get("projectGoals") or profile.get("ProjectGoals", "")
        )
        skills_data = profile.get("skills") or profile.get("Skills", [])

        # Normalize skills to extract ratings
        student_skills = []
        average_rating = 70.0
        skill_ratings = []

        for skill in skills_data:
            if isinstance(skill, dict):
                skill_name = skill.get("skillName") or skill.get("SkillName", "")
                rating = float(skill.get("rating") or skill.get("Rating", 70))
                student_skills.append(skill_name)
                skill_ratings.append(rating)
            else:
                student_skills.append(str(skill))

        if skill_ratings:
            average_rating = sum(skill_ratings) / len(skill_ratings)

        # Calculate base scores
        innovation_score = self._calculate_innovation_score(
            average_rating, target_difficulty, preferred_domain
        )
        feasibility_score = self._calculate_feasibility_score(
            available_hours, team_members, target_difficulty, experience_level
        )
        market_demand_score = self._calculate_market_demand_score(
            preferred_domain, average_rating, major
        )

        # Domain-specific idea templates
        domain_ideas = {
            "web": [
                {
                    "title": "E-Learning Platform for Lebanese Schools",
                    "problemStatement": "Lebanese schools lack accessible digital learning platforms tailored to Arabic content and local curriculum.",
                    "targetUsers": "Primary/Secondary school students and teachers in Lebanon",
                    "domain": "web",
                },
                {
                    "title": "Smart Agriculture Marketplace",
                    "problemStatement": "Lebanese farmers have limited access to direct markets for their produce.",
                    "targetUsers": "Farmers and consumers in Lebanon",
                    "domain": "web",
                },
                {
                    "title": "Local Event Management System",
                    "problemStatement": "Community events in Lebanon lack centralized management and promotion platforms.",
                    "targetUsers": "Event organizers and attendees",
                    "domain": "web",
                },
            ],
            "ai_ml": [
                {
                    "title": "Arabic Sentiment Analysis for Social Media",
                    "problemStatement": "Lack of accurate sentiment analysis tools for Arabic social media content.",
                    "targetUsers": "Businesses, marketers, researchers",
                    "domain": "ai_ml",
                },
                {
                    "title": "Lebanese Job Recommendation Engine",
                    "problemStatement": "Job seekers struggle to find positions matching their skills in Lebanon.",
                    "targetUsers": "Job seekers and employers in Lebanon",
                    "domain": "ai_ml",
                },
                {
                    "title": "Student Performance Predictor",
                    "problemStatement": "Educational institutions need AI to predict and support at-risk students.",
                    "targetUsers": "Schools, universities, educational officials",
                    "domain": "ai_ml",
                },
            ],
            "mobile": [
                {
                    "title": "Traffic Monitoring Mobile App",
                    "problemStatement": "Beirut residents need real-time traffic and alternative route suggestions.",
                    "targetUsers": "Commuters in Lebanese cities",
                    "domain": "mobile",
                },
                {
                    "title": "Lebanese News Aggregator",
                    "problemStatement": "Users struggle to get consolidated news from multiple Lebanese sources.",
                    "targetUsers": "News consumers in Lebanon",
                    "domain": "mobile",
                },
                {
                    "title": "Healthcare Appointment Booking App",
                    "problemStatement": "Lebanese patients lack centralized app to book medical appointments.",
                    "targetUsers": "Patients and healthcare providers",
                    "domain": "mobile",
                },
            ],
            "data_science": [
                {
                    "title": "Lebanese Economic Trend Analysis",
                    "problemStatement": "Need predictive models for Lebanon's economic indicators.",
                    "targetUsers": "Economists, policymakers, investors",
                    "domain": "data_science",
                },
                {
                    "title": "Tourism Data Analytics Platform",
                    "problemStatement": "Lebanon's tourism sector lacks data-driven insights.",
                    "targetUsers": "Tourism board, hotels, tour operators",
                    "domain": "data_science",
                },
                {
                    "title": "Real Estate Market Analysis",
                    "problemStatement": "Property buyers need data-driven insights on Lebanese real estate trends.",
                    "targetUsers": "Real estate professionals and buyers",
                    "domain": "data_science",
                },
            ],
        }

        # Get ideas for the domain, fallback to web if not found
        selected_ideas = domain_ideas.get(preferred_domain.lower(), domain_ideas["web"])

        # Enhance ideas with calculated scores and additional fields
        required_skills = self._get_required_skills(preferred_domain)
        missing_skills = self._get_missing_skills(required_skills, student_skills)

        enhanced_ideas = []
        for i, idea_template in enumerate(selected_ideas[:3]):
            enhanced_idea = {
                "title": idea_template["title"],
                "problemStatement": idea_template["problemStatement"],
                "targetUsers": idea_template["targetUsers"],
                "whyUseful": f"Addresses real market needs in Lebanon with high commercial potential.",
                "lebaneseMarketRelevance": f"High relevance to Lebanese context and available opportunities.",
                "requiredTechnologies": required_skills,
                "requiredSkills": required_skills,
                "missingSkills": missing_skills,
                "difficultyLevel": target_difficulty,
                "innovationScore": innovation_score - (i * 5),
                "feasibilityScore": feasibility_score - (i * 3),
                "marketDemandScore": market_demand_score - (i * 2),
                "expectedDurationWeeks": self._estimate_duration(
                    target_difficulty, team_members, available_hours
                ),
                "supervisorCategory": self._get_supervisor_category(preferred_domain),
                "datasetNeeded": self._get_dataset_needs(preferred_domain),
                "finalDeliverables": self._get_deliverables(preferred_domain),
                "domain": preferred_domain,
                "lebaneseSector": self._get_lebanese_sector(preferred_domain),
            }
            enhanced_ideas.append(enhanced_idea)

        return enhanced_ideas

    def _estimate_duration(
        self, difficulty: str, team_size: int, hours_per_week: float
    ) -> int:
        """Estimate project duration in weeks."""
        base_duration = {
            "beginner": 8,
            "intermediate": 12,
            "advanced": 16,
            "expert": 20,
        }
        duration = base_duration.get(difficulty.lower(), 12)

        team_factor = max(1.0, 2.0 / team_size)
        hours_factor = hours_per_week / 15

        estimated = int(duration * team_factor / max(hours_factor, 0.5))
        return max(estimated, 6)

    def _get_supervisor_category(self, domain: str) -> str:
        """Determine supervisor category based on domain."""
        mapping = {
            "web": "Web & Cloud",
            "mobile": "Mobile Development",
            "ai_ml": "AI/ML",
            "data_science": "Data Science",
            "iot": "IoT & Embedded Systems",
            "blockchain": "Blockchain",
            "cybersecurity": "Cybersecurity",
            "cloud": "Cloud Infrastructure",
        }
        return mapping.get(domain.lower(), "Software Engineering")

    def _get_dataset_needs(self, domain: str) -> str:
        """Get dataset requirements for the domain."""
        needs = {
            "ai_ml": "Large labeled dataset; access to public ML datasets (Kaggle, UCI)",
            "data_science": "Historical data sources; APIs for real-time data",
            "web": "Sample data for testing; user feedback datasets",
            "mobile": "Usage analytics; device compatibility data",
            "blockchain": "Blockchain transaction data; smart contract samples",
            "cybersecurity": "Security logs; threat intelligence feeds",
            "iot": "Sensor data streams; IoT device logs",
            "cloud": "Performance metrics; cloud infrastructure data",
        }
        return needs.get(domain.lower(), "Standard project datasets")

    def _get_deliverables(self, domain: str) -> List[str]:
        """Get expected deliverables for the domain."""
        deliverables = {
            "web": [
                "Working web application deployed on live server",
                "Complete source code with documentation",
                "Database schema and migrations",
                "User authentication and authorization system",
            ],
            "mobile": [
                "Fully functional mobile app (iOS and/or Android)",
                "App store submission-ready package",
                "API documentation",
                "User guide and installation instructions",
            ],
            "ai_ml": [
                "Trained machine learning model",
                "Model evaluation report with metrics",
                "Training data documentation",
                "Python notebook demonstrating inference",
            ],
            "data_science": [
                "Data analysis reports with visualizations",
                "Statistical findings and insights",
                "Predictive models or dashboards",
                "Reproducible analysis scripts",
            ],
            "blockchain": [
                "Smart contracts (audited)",
                "Blockchain application frontend",
                "Contract deployment guide",
                "Security audit report",
            ],
            "cybersecurity": [
                "Penetration test report",
                "Security tools/scripts developed",
                "Vulnerability assessment results",
                "Security recommendations document",
            ],
            "iot": [
                "Working IoT prototype",
                "Embedded system code",
                "Dashboard or control interface",
                "Hardware integration documentation",
            ],
            "cloud": [
                "Deployed cloud infrastructure",
                "Infrastructure as Code (IaC)",
                "Deployment and scaling documentation",
                "Performance benchmarks",
            ],
        }
        return deliverables.get(domain.lower(), ["Working application", "Documentation"])

    def _get_lebanese_sector(self, domain: str) -> str:
        """Map domain to Lebanese economic sector."""
        mapping = {
            "web": "Information Technology & Services",
            "mobile": "Information Technology & Services",
            "ai_ml": "Research & Development",
            "data_science": "Analytics & Consulting",
            "iot": "Manufacturing & Technology",
            "blockchain": "Financial Services & Technology",
            "cybersecurity": "Defense & Information Security",
            "cloud": "Information Technology Infrastructure",
        }
        return mapping.get(domain.lower(), "Technology Sector")


# Initialize agent
agent = ProjectIdeaAgent()


@router.post("/generate-ideas")
def generate_project_ideas(body: Dict[str, Any]):
    """
    Generate intelligent FYP project ideas based on student profile.
    
    Request body:
    {
        "major": "Computer Science",
        "experienceLevel": "intermediate",
        "preferredDomain": "web",
        "targetDifficulty": "intermediate",
        "preferredStack": "Python, React",
        "availableHoursPerWeek": 20,
        "teamMembers": 2,
        "projectGoals": "Build something useful for Lebanon",
        "skills": [
            {
                "skillName": "Python",
                "rating": 85,
                "proficiencyLevel": "advanced"
            },
            {
                "skillName": "React",
                "rating": 70,
                "proficiencyLevel": "intermediate"
            }
        ]
    }
    
    Returns:
        List of 3 project ideas with scores, technologies, and detailed information.
    """
    try:
        ideas = agent.generate_ideas(body)
        return {
            "status": "success",
            "ideas_count": len(ideas),
            "ideas": ideas,
        }
    except Exception as e:
        logger.error(f"Error generating ideas: {e}")
        return {
            "status": "error",
            "message": str(e),
            "ideas": [],
        }
