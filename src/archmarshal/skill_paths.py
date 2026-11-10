"""Discovery roots shared by adoption and import boundary validation."""
SKILL_ROOT_CANDIDATES = (
    ".agents",
    ".codex/skills",
    ".claude/skills",
    "plugins",
    "skills",
)
SKILL_PATH_FIELDS = (
    "global_skills",
    "functional_skills",
    "common_project_skills",
    "project_skills",
    "generated_skills",
)
