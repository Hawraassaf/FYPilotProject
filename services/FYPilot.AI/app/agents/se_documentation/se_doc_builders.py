import re
from typing import Any, Dict, List, Set


class SEDocDeterministicBuilder:
    """
    Cleans and validates LLM output.

    This class protects the final document from broken LLM references.

    It fixes:
    - invalid FR references
    - invalid entity relationships
    - invalid traceability rows
    - Mermaid diagrams
    - quality score
    """

    def build_clean_sections(self, sections: Dict[str, Any]) -> Dict[str, Any]:
        requirements = sections.get("requirements", {}) or {}
        use_case_section = sections.get("useCases", {}) or {}
        modules_section = sections.get("modules", {}) or {}
        database_section = sections.get("database", {}) or {}
        testing_section = sections.get("testing", {}) or {}

        functional_requirements = self.clean_requirements(
            raw=requirements.get("functionalRequirements", []),
            prefix="FR",
            count=8,
            fallback=self.fallback_functional_requirements(),
        )

        non_functional_requirements = self.clean_requirements(
            raw=requirements.get("nonFunctionalRequirements", []),
            prefix="NFR",
            count=5,
            fallback=self.fallback_nonfunctional_requirements(),
        )

        valid_fr_ids = {item["id"] for item in functional_requirements}

        use_cases = self.clean_use_cases(
            raw=use_case_section.get("useCases", []),
            valid_fr_ids=valid_fr_ids,
        )

        edge_cases = self.clean_edge_cases(
            raw=use_case_section.get("edgeCases", []),
            valid_fr_ids=valid_fr_ids,
        )

        modules = self.clean_modules(
            raw=modules_section.get("systemModules", []),
            valid_fr_ids=valid_fr_ids,
        )

        database_entities = self.clean_entities(
            raw=database_section.get("databaseEntities", []),
        )

        entity_relationships = self.clean_relationships(
            raw=database_section.get("entityRelationships", []),
            entities=database_entities,
        )

        testing_plan = self.clean_tests(
            raw=testing_section.get("testingPlan", []),
            valid_fr_ids=valid_fr_ids,
        )

        traceability_matrix = self.build_traceability_matrix(
            functional_requirements=functional_requirements,
            use_cases=use_cases,
            modules=modules,
            entities=database_entities,
            tests=testing_plan,
        )

        return {
            "functionalRequirements": functional_requirements,
            "nonFunctionalRequirements": non_functional_requirements,
            "useCases": use_cases,
            "edgeCases": edge_cases,
            "systemModules": modules,
            "databaseEntities": database_entities,
            "entityRelationships": entity_relationships,
            "traceabilityMatrix": traceability_matrix,
            "mermaidERD": self.build_erd(database_entities, entity_relationships),
            "mermaidClassDiagram": self.build_class_diagram(database_entities),
            "activityDiagram": self.build_activity_diagram(),
            "sequenceDiagram": self.build_sequence_diagram(),
            "documentationQualityScore": self.compute_quality_score(
                functional_requirements,
                non_functional_requirements,
                use_cases,
                modules,
                database_entities,
                testing_plan,
                traceability_matrix,
            ),
        }

    def clean_requirements(
        self,
        raw: Any,
        prefix: str,
        count: int,
        fallback: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        if not isinstance(raw, list):
            raw = []

        cleaned: List[Dict[str, Any]] = []

        for index in range(count):
            item = raw[index] if index < len(raw) and isinstance(raw[index], dict) else {}

            cleaned.append(
                {
                    "id": f"{prefix}-{index + 1:02d}",
                    "title": self.clean_text(
                        item.get("title")
                        or fallback[index].get("title")
                    ),
                    "description": self.clean_text(
                        item.get("description")
                        or fallback[index].get("description")
                    ),
                    "priority": self.clean_priority(
                        item.get("priority")
                        or fallback[index].get("priority")
                        or "High"
                    ),
                    "source": self.clean_text(
                        item.get("source")
                        or fallback[index].get("source")
                        or "System analysis"
                    ),
                }
            )

        return cleaned

    def clean_use_cases(
        self,
        raw: Any,
        valid_fr_ids: Set[str],
    ) -> List[Dict[str, Any]]:
        fallback = self.fallback_use_cases()

        if not isinstance(raw, list):
            raw = []

        result = []

        for index in range(5):
            item = raw[index] if index < len(raw) and isinstance(raw[index], dict) else fallback[index]

            result.append(
                {
                    "id": f"UC-{index + 1:02d}",
                    "title": self.clean_text(item.get("title") or fallback[index]["title"]),
                    "actor": self.clean_text(item.get("actor") or "Student"),
                    "goal": self.clean_text(item.get("goal") or fallback[index]["goal"]),
                    "preconditions": self.clean_string_list(item.get("preconditions") or fallback[index]["preconditions"]),
                    "mainFlow": self.clean_string_list(item.get("mainFlow") or fallback[index]["mainFlow"]),
                    "alternativeFlow": self.clean_string_list(item.get("alternativeFlow") or fallback[index]["alternativeFlow"]),
                    "postconditions": self.clean_string_list(item.get("postconditions") or fallback[index]["postconditions"]),
                    "relatedRequirements": self.clean_requirement_refs(
                        item.get("relatedRequirements"),
                        valid_fr_ids,
                        default=f"FR-{min(index + 1, 8):02d}",
                    ),
                }
            )

        return result

    def clean_edge_cases(
        self,
        raw: Any,
        valid_fr_ids: Set[str],
    ) -> List[Dict[str, Any]]:
        fallback = self.fallback_edge_cases()

        if not isinstance(raw, list):
            raw = []

        result = []

        for index in range(5):
            item = raw[index] if index < len(raw) and isinstance(raw[index], dict) else fallback[index]

            related = item.get("relatedRequirement") or f"FR-{min(index + 1, 8):02d}"

            if related not in valid_fr_ids:
                related = f"FR-{min(index + 1, 8):02d}"

            result.append(
                {
                    "id": f"EC-{index + 1:02d}",
                    "scenario": self.clean_text(item.get("scenario") or fallback[index]["scenario"]),
                    "expectedHandling": self.clean_text(item.get("expectedHandling") or fallback[index]["expectedHandling"]),
                    "relatedRequirement": related,
                }
            )

        return result

    def clean_modules(
        self,
        raw: Any,
        valid_fr_ids: Set[str],
    ) -> List[Dict[str, Any]]:
        fallback = self.fallback_modules()

        if not isinstance(raw, list):
            raw = []

        result = []

        for index in range(5):
            item = raw[index] if index < len(raw) and isinstance(raw[index], dict) else fallback[index]

            result.append(
                {
                    "id": f"M-{index + 1:02d}",
                    "name": self.clean_text(item.get("name") or fallback[index]["name"]),
                    "responsibility": self.clean_text(item.get("responsibility") or fallback[index]["responsibility"]),
                    "inputs": self.clean_string_list(item.get("inputs") or fallback[index]["inputs"]),
                    "outputs": self.clean_string_list(item.get("outputs") or fallback[index]["outputs"]),
                    "relatedRequirements": self.clean_requirement_refs(
                        item.get("relatedRequirements"),
                        valid_fr_ids,
                        default=f"FR-{min(index + 1, 8):02d}",
                    ),
                }
            )

        return result

    def clean_entities(self, raw: Any) -> List[Dict[str, Any]]:
        fallback = self.fallback_entities()

        if not isinstance(raw, list):
            raw = []

        result = []

        used_names = set()

        for index in range(5):
            item = raw[index] if index < len(raw) and isinstance(raw[index], dict) else fallback[index]

            name = self.clean_entity_name(item.get("name") or fallback[index]["name"])

            if name in used_names:
                name = fallback[index]["name"]

            used_names.add(name)

            result.append(
                {
                    "name": name,
                    "purpose": self.clean_text(item.get("purpose") or fallback[index]["purpose"]),
                    "importantFields": self.clean_string_list(item.get("importantFields") or fallback[index]["importantFields"])[:6],
                    "relationships": self.clean_string_list(item.get("relationships") or fallback[index]["relationships"])[:5],
                }
            )

        return result

    def clean_relationships(
        self,
        raw: Any,
        entities: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        entity_names = {entity["name"] for entity in entities}

        if not isinstance(raw, list):
            raw = []

        result = []

        for item in raw:
            if not isinstance(item, dict):
                continue

            from_entity = self.clean_entity_name(item.get("fromEntity", ""))
            to_entity = self.clean_entity_name(item.get("toEntity", ""))

            if from_entity not in entity_names or to_entity not in entity_names:
                continue

            if from_entity == to_entity:
                continue

            result.append(
                {
                    "fromEntity": from_entity,
                    "toEntity": to_entity,
                    "type": self.clean_relationship_type(item.get("type", "one-to-many")),
                    "description": self.clean_text(item.get("description") or f"{from_entity} relates to {to_entity}"),
                }
            )

        if not result:
            result = self.fallback_relationships(entities)

        return result[:6]

    def clean_tests(
        self,
        raw: Any,
        valid_fr_ids: Set[str],
    ) -> List[Dict[str, Any]]:
        fallback = self.fallback_tests()

        if not isinstance(raw, list):
            raw = []

        result = []

        for index in range(5):
            item = raw[index] if index < len(raw) and isinstance(raw[index], dict) else fallback[index]

            result.append(
                {
                    "id": f"TC-{index + 1:02d}",
                    "title": self.clean_text(item.get("title") or fallback[index]["title"]),
                    "type": self.clean_text(item.get("type") or fallback[index]["type"]),
                    "steps": self.clean_string_list(item.get("steps") or fallback[index]["steps"]),
                    "expectedResult": self.clean_text(item.get("expectedResult") or fallback[index]["expectedResult"]),
                    "relatedRequirements": self.clean_requirement_refs(
                        item.get("relatedRequirements"),
                        valid_fr_ids,
                        default=f"FR-{min(index + 1, 8):02d}",
                    ),
                }
            )

        return result

    def build_traceability_matrix(
        self,
        functional_requirements: List[Dict[str, Any]],
        use_cases: List[Dict[str, Any]],
        modules: List[Dict[str, Any]],
        entities: List[Dict[str, Any]],
        tests: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        rows = []

        for index in range(5):
            rows.append(
                {
                    "requirementId": functional_requirements[min(index, len(functional_requirements) - 1)]["id"],
                    "useCaseId": use_cases[min(index, len(use_cases) - 1)]["id"],
                    "moduleId": modules[min(index, len(modules) - 1)]["id"],
                    "entity": entities[min(index, len(entities) - 1)]["name"],
                    "testCaseId": tests[min(index, len(tests) - 1)]["id"],
                }
            )

        return rows

    def build_erd(
        self,
        entities: List[Dict[str, Any]],
        relationships: List[Dict[str, Any]],
    ) -> str:
        lines = ["erDiagram"]

        entity_names = {entity["name"] for entity in entities}

        for relationship in relationships:
            from_entity = relationship["fromEntity"]
            to_entity = relationship["toEntity"]

            if from_entity not in entity_names or to_entity not in entity_names:
                continue

            symbol = "||--o{" if "many" in relationship.get("type", "") else "||--||"

            lines.append(
                f"    {self.mermaid_name(from_entity)} {symbol} {self.mermaid_name(to_entity)} : relates"
            )

        if len(lines) == 1 and len(entities) >= 2:
            lines.append(
                f"    {self.mermaid_name(entities[0]['name'])} ||--o{{ {self.mermaid_name(entities[1]['name'])} : owns"
            )

        return "\n".join(lines)

    def build_class_diagram(self, entities: List[Dict[str, Any]]) -> str:
        lines = ["classDiagram"]

        for entity in entities:
            entity_name = self.mermaid_name(entity["name"])
            lines.append(f"    class {entity_name} {{")

            for field in entity.get("importantFields", [])[:6]:
                cleaned_field = re.sub(r"[^A-Za-z0-9_]", "", str(field))

                if not cleaned_field:
                    cleaned_field = "Field"

                lines.append(f"      string {cleaned_field}")

            lines.append("    }")

        return "\n".join(lines)

    def build_activity_diagram(self) -> str:
        return """flowchart TD
    A[Student logs in] --> B[Complete profile]
    B --> C[Generate project ideas]
    C --> D[Compare ideas]
    D --> E[Select final idea]
    E --> F[Generate roadmap]
    F --> G[Generate SE documentation]
    G --> H[Review and improve documentation]
"""

    def build_sequence_diagram(self) -> str:
        return """sequenceDiagram
    actor Student
    participant Web as ASP.NET Razor Pages
    participant DB as PostgreSQL
    participant AI as Python FastAPI
    participant LLM as Ollama
    Student->>Web: Click Generate SE Documentation
    Web->>DB: Load profile, selected idea, skills, and roadmap
    Web->>AI: POST /generate-se-documentation
    AI->>LLM: Generate small documentation sections
    LLM-->>AI: Return JSON sections
    AI->>AI: Validate and assemble final document
    AI-->>Web: Return validated documentation
    Web-->>Student: Display documentation
"""

    def compute_quality_score(
        self,
        functional_requirements: List[Dict[str, Any]],
        non_functional_requirements: List[Dict[str, Any]],
        use_cases: List[Dict[str, Any]],
        modules: List[Dict[str, Any]],
        entities: List[Dict[str, Any]],
        tests: List[Dict[str, Any]],
        traceability: List[Dict[str, Any]],
    ) -> int:
        score = 60

        if len(functional_requirements) >= 8:
            score += 8

        if len(non_functional_requirements) >= 5:
            score += 6

        if len(use_cases) >= 5:
            score += 6

        if len(modules) >= 5:
            score += 5

        if len(entities) >= 5:
            score += 5

        if len(tests) >= 5:
            score += 5

        if len(traceability) >= 5:
            score += 5

        return max(0, min(100, score))

    def clean_requirement_refs(
        self,
        value: Any,
        valid_fr_ids: Set[str],
        default: str,
    ) -> List[str]:
        if isinstance(value, str):
            refs = [value]
        elif isinstance(value, list):
            refs = [str(item) for item in value]
        else:
            refs = []

        cleaned = []

        for ref in refs:
            ref = ref.strip().upper()

            if ref in valid_fr_ids:
                cleaned.append(ref)

        if not cleaned:
            cleaned = [default if default in valid_fr_ids else "FR-01"]

        return list(dict.fromkeys(cleaned))

    def clean_text(self, value: Any) -> str:
        if value is None:
            return ""

        text = str(value).strip()
        text = re.sub(r"\s+", " ", text)

        return text

    def clean_string_list(self, value: Any) -> List[str]:
        if value is None:
            return []

        if isinstance(value, list):
            return [
                self.clean_text(item)
                for item in value
                if self.clean_text(item)
            ]

        if isinstance(value, str):
            return [
                item.strip()
                for item in re.split(r"[,;\n|]+", value)
                if item.strip()
            ]

        return [self.clean_text(value)] if self.clean_text(value) else []

    def clean_priority(self, value: Any) -> str:
        text = self.clean_text(value).lower()

        if text in ["high", "medium", "low"]:
            return text.capitalize()

        return "High"

    def clean_relationship_type(self, value: Any) -> str:
        text = self.clean_text(value).lower()

        valid_types = {
            "one-to-one",
            "one-to-many",
            "many-to-one",
            "many-to-many",
        }

        if text in valid_types:
            return text

        return "one-to-many"

    def clean_entity_name(self, value: Any) -> str:
        text = self.clean_text(value)

        if not text:
            return "Entity"

        text = re.sub(r"[^A-Za-z0-9_]", "", text)

        if not text:
            return "Entity"

        return text[0].upper() + text[1:]

    def mermaid_name(self, value: str) -> str:
        cleaned = re.sub(r"[^A-Za-z0-9_]", "_", value.strip())
        return cleaned.upper() or "ENTITY"

    def fallback_functional_requirements(self) -> List[Dict[str, Any]]:
        return [
            {"title": "Manage authentication", "description": "The system shall allow students to register, log in, log out, and access protected pages.", "priority": "High", "source": "Security"},
            {"title": "Maintain student profile", "description": "The system shall store student academic information, skills, experience level, team size, and available weekly hours.", "priority": "High", "source": "Student profile"},
            {"title": "Generate project ideas", "description": "The system shall generate personalized project ideas based on student profile, skills, domain, and constraints.", "priority": "High", "source": "Idea generation"},
            {"title": "Compare generated ideas", "description": "The system shall compare ideas using feasibility, innovation, market relevance, and skill fit.", "priority": "High", "source": "Idea comparison"},
            {"title": "Select final project idea", "description": "The system shall allow the student to select one idea as the active final year project.", "priority": "High", "source": "Project workflow"},
            {"title": "Generate roadmap", "description": "The system shall generate roadmap phases, tasks, expected outputs, and success criteria.", "priority": "High", "source": "Roadmap"},
            {"title": "Provide mentor chat", "description": "The system shall answer student questions using selected idea, roadmap, and skill context.", "priority": "Medium", "source": "Mentor chat"},
            {"title": "Generate SE documentation", "description": "The system shall generate requirements, use cases, modules, entities, diagrams, test cases, and traceability matrix.", "priority": "High", "source": "SE documentation"},
        ]

    def fallback_nonfunctional_requirements(self) -> List[Dict[str, Any]]:
        return [
            {"title": "Security", "description": "Students must only access their own profiles, ideas, roadmaps, chats, and generated documentation.", "priority": "High", "source": "Software quality"},
            {"title": "Usability", "description": "The interface should be simple, organized, and suitable for final year students.", "priority": "High", "source": "Software quality"},
            {"title": "Reliability", "description": "The system should provide fallback output if the local AI model fails or times out.", "priority": "High", "source": "Software quality"},
            {"title": "Performance", "description": "The system should return normal page responses quickly and handle long AI generation using background jobs.", "priority": "Medium", "source": "Software quality"},
            {"title": "Maintainability", "description": "The system should separate UI, DTOs, services, database access, and AI agents.", "priority": "High", "source": "Software quality"},
        ]

    def fallback_use_cases(self) -> List[Dict[str, Any]]:
        return [
            {"title": "Complete profile", "actor": "Student", "goal": "Provide academic and project context.", "preconditions": ["Student is logged in."], "mainFlow": ["Open profile page.", "Enter details.", "Save profile."], "alternativeFlow": ["System shows validation if required fields are missing."], "postconditions": ["Profile is saved."]},
            {"title": "Generate project ideas", "actor": "Student", "goal": "Receive suitable project ideas.", "preconditions": ["Student profile exists."], "mainFlow": ["Open idea generator.", "Click generate.", "Review generated ideas."], "alternativeFlow": ["Fallback ideas appear if AI service fails."], "postconditions": ["Ideas are stored."]},
            {"title": "Compare ideas", "actor": "Student", "goal": "Choose the strongest idea.", "preconditions": ["At least two ideas exist."], "mainFlow": ["Open comparison page.", "Click compare.", "Review ranking."], "alternativeFlow": ["Warning appears if not enough ideas exist."], "postconditions": ["Comparison is displayed."]},
            {"title": "Select final idea", "actor": "Student", "goal": "Mark one idea as active.", "preconditions": ["Generated ideas exist."], "mainFlow": ["Choose an idea.", "Click select.", "System marks it selected."], "alternativeFlow": ["Unauthorized ideas are rejected."], "postconditions": ["Selected idea becomes active."]},
            {"title": "Generate SE documentation", "actor": "Student", "goal": "Create documentation for selected idea.", "preconditions": ["Selected idea exists."], "mainFlow": ["Open documentation page.", "Click generate.", "Review sections."], "alternativeFlow": ["Fallback documentation appears if AI fails."], "postconditions": ["Documentation is displayed."]},
        ]

    def fallback_edge_cases(self) -> List[Dict[str, Any]]:
        return [
            {"scenario": "No selected idea exists.", "expectedHandling": "Ask the student to select an idea first."},
            {"scenario": "AI service is offline.", "expectedHandling": "Return fallback documentation and show a clear warning."},
            {"scenario": "Student has no saved skills.", "expectedHandling": "Use general assumptions and warn the student."},
            {"scenario": "Generated idea has vague problem statement.", "expectedHandling": "Generate cautious documentation and include a warning."},
            {"scenario": "Student tries to access another user's project.", "expectedHandling": "Reject the request and protect user data."},
        ]

    def fallback_modules(self) -> List[Dict[str, Any]]:
        return [
            {"name": "Profile Module", "responsibility": "Manages student profile and skill data.", "inputs": ["Profile data", "Skills"], "outputs": ["Saved profile"]},
            {"name": "Idea Generation Module", "responsibility": "Generates personalized project ideas.", "inputs": ["Profile", "Skills", "Preferences"], "outputs": ["Generated ideas"]},
            {"name": "Idea Comparison Module", "responsibility": "Ranks ideas and recommends the best one.", "inputs": ["Ideas", "Student context"], "outputs": ["Comparison result"]},
            {"name": "Roadmap Module", "responsibility": "Creates a project implementation roadmap.", "inputs": ["Selected idea", "Timeline"], "outputs": ["Roadmap phases"]},
            {"name": "SE Documentation Module", "responsibility": "Generates software engineering artifacts.", "inputs": ["Idea", "Profile", "Roadmap"], "outputs": ["Documentation"]},
        ]

    def fallback_entities(self) -> List[Dict[str, Any]]:
        return [
            {"name": "User", "purpose": "Stores account and authentication information.", "importantFields": ["Id", "Email", "PasswordHash", "Role"], "relationships": ["User has one StudentProfile", "User has many ProjectIdeas"]},
            {"name": "StudentProfile", "purpose": "Stores student academic and project planning information.", "importantFields": ["Id", "UserId", "Major", "ExperienceLevel", "AvailableHoursPerWeek"], "relationships": ["StudentProfile belongs to User"]},
            {"name": "StudentSkill", "purpose": "Stores technical skills and ratings.", "importantFields": ["Id", "UserId", "SkillName", "Rating"], "relationships": ["StudentSkill belongs to User"]},
            {"name": "ProjectIdea", "purpose": "Stores generated project ideas and selected idea status.", "importantFields": ["Id", "UserId", "Title", "ProblemStatement", "IsSelected"], "relationships": ["ProjectIdea belongs to User"]},
            {"name": "SEDocumentation", "purpose": "Stores generated SE documentation.", "importantFields": ["Id", "UserId", "ProjectIdeaId", "ContentJson", "QualityScore"], "relationships": ["SEDocumentation belongs to ProjectIdea"]},
        ]

    def fallback_relationships(self, entities: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        names = [entity["name"] for entity in entities]

        relationships = []

        if "User" in names and "StudentProfile" in names:
            relationships.append({"fromEntity": "User", "toEntity": "StudentProfile", "type": "one-to-one", "description": "A student user has one profile."})

        if "User" in names and "StudentSkill" in names:
            relationships.append({"fromEntity": "User", "toEntity": "StudentSkill", "type": "one-to-many", "description": "A student can have many skills."})

        if "User" in names and "ProjectIdea" in names:
            relationships.append({"fromEntity": "User", "toEntity": "ProjectIdea", "type": "one-to-many", "description": "A student can generate many ideas."})

        if "ProjectIdea" in names and "SEDocumentation" in names:
            relationships.append({"fromEntity": "ProjectIdea", "toEntity": "SEDocumentation", "type": "one-to-many", "description": "A project idea can have documentation versions."})

        if relationships:
            return relationships

        if len(entities) >= 2:
            return [
                {
                    "fromEntity": entities[0]["name"],
                    "toEntity": entities[1]["name"],
                    "type": "one-to-many",
                    "description": "Default relationship between main entities.",
                }
            ]

        return []

    def fallback_tests(self) -> List[Dict[str, Any]]:
        return [
            {"title": "Profile save test", "type": "Functional", "steps": ["Open profile page.", "Enter valid data.", "Click save."], "expectedResult": "Profile is saved successfully."},
            {"title": "Idea generation test", "type": "AI Integration", "steps": ["Open idea generator.", "Click generate."], "expectedResult": "Project ideas are generated and displayed."},
            {"title": "Idea comparison test", "type": "AI Integration", "steps": ["Generate at least two ideas.", "Click compare."], "expectedResult": "Ranked comparison appears."},
            {"title": "Roadmap generation test", "type": "AI Integration", "steps": ["Select an idea.", "Generate roadmap."], "expectedResult": "Roadmap phases are displayed."},
            {"title": "SE documentation generation test", "type": "AI Integration", "steps": ["Select idea.", "Generate documentation."], "expectedResult": "Documentation sections are displayed."},
        ]